<template>
  <div
    class="flex flex-col gap-3 rounded-lg border border-outline-gray-2 bg-surface-gray-1 p-4"
  >
    <div class="flex items-center justify-between gap-3">
      <div class="flex flex-col gap-0.5">
        <span class="text-sm font-medium text-ink-gray-8">
          {{ __("Transcript Fee") }}
        </span>
        <span v-if="state === 'paid' && resumed" class="text-xs text-ink-green-3">
          {{ __("We found a payment you already made for this request — no need to pay again.") }}
        </span>
        <span v-else-if="state === 'paid'" class="text-xs text-ink-green-3">
          {{ __("Payment received — payment ID {0}", paymentId) }}
        </span>
        <span v-else-if="feeAmount !== null" class="text-xs text-ink-gray-5">
          {{
            paymentRequired
              ? __("Payment is required before this request can be submitted.")
              : __("No payment required for this request.")
          }}
        </span>
      </div>
      <span
        v-if="feeAmount !== null"
        class="text-lg font-semibold text-ink-gray-8"
      >
        ₹{{ feeAmount }}
      </span>
    </div>

    <div v-if="errorMessage" class="text-sm text-ink-red-3">
      {{ errorMessage }}
    </div>

    <Button
      v-if="paymentRequired && state !== 'paid'"
      :label="payButtonLabel"
      theme="gray"
      variant="solid"
      :loading="state === 'creating' || state === 'ordering' || state === 'verifying'"
      :disabled="!ready || state === 'creating' || state === 'ordering' || state === 'verifying'"
      @click="startPayment"
    />
    <div
      v-if="paymentRequired && state !== 'paid'"
      class="flex items-start gap-1.5 text-xs text-ink-gray-5"
    >
      <FeatherIcon name="info" class="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span>
        {{
          __(
            "If your payment succeeds but the page closes or reloads before you submit, don't pay again — come back to this form and we'll pick up right where you left off."
          )
        }}
      </span>
    </div>
    <div v-else-if="state === 'paid'" class="flex items-center gap-1.5 text-sm text-ink-green-3">
      <FeatherIcon name="check-circle" class="h-4 w-4" />
      {{ __("Ready to submit") }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { Button, FeatherIcon, call } from "frappe-ui";
import { computed, onMounted, ref, watch } from "vue";
import { __ } from "@/translation";

interface P {
  transcriptType: string;
  numCopies: number | string;
  purpose: string;
  deliveryMode: string;
  // Bumped by the parent whenever an input the fee depends on changes, so
  // this panel knows to throw away a stale unpaid Transcript Request and
  // start over rather than let the student pay for the wrong thing.
  resetKey: number;
}

interface E {
  (
    event: "paid",
    payload: {
      requestName: string;
      feeAmount: number;
      transcriptType: string;
      numCopies: number | string;
      // Only set on resume, so a fresh payment doesn't clobber whatever the
      // student is still typing in those fields with a stale echo.
      purpose?: string;
      deliveryMode?: string;
    }
  ): void;
  (event: "reset"): void;
}

const props = defineProps<P>();
const emit = defineEmits<E>();

// idle -> creating (Transcript Request draft) -> ordering (Razorpay order)
// -> awaiting (checkout open) -> verifying (confirm_payment) -> paid
// any step can fall back to "error"
const state = ref<
  "idle" | "creating" | "ordering" | "awaiting" | "verifying" | "paid" | "error"
>("idle");
const feeAmount = ref<number | null>(null);
const paymentRequired = ref(false);
const requestName = ref<string | null>(null);
const paymentId = ref<string | null>(null);
const errorMessage = ref("");
// The transcript type/copies the current request/payment was actually
// created for — sourced from the server response, not props, since props
// can keep changing while a payment is in flight.
const paidForType = ref<string | null>(null);
const paidForCopies = ref<number | string | null>(null);

const ready = computed(
  () =>
    Boolean(props.transcriptType) &&
    Boolean(props.purpose && props.purpose.trim()) &&
    Number(props.numCopies) > 0
);

const payButtonLabel = computed(() => {
  if (state.value === "creating") return __("Preparing…");
  if (state.value === "ordering") return __("Opening payment…");
  if (state.value === "verifying") return __("Verifying payment…");
  return feeAmount.value !== null
    ? __("Pay ₹{0}", String(feeAmount.value))
    : __("Pay");
});

// Re-fetch the fee preview whenever the inputs it depends on change, but
// don't touch an already-created (or already-paid) request just because the
// student is still typing in an unrelated field.
watch(
  () => [props.transcriptType, props.numCopies],
  () => {
    if (state.value === "idle") fetchPreview();
  }
);

watch(
  () => props.resetKey,
  () => {
    // Parent detected a change that invalidates any request already created
    // for this attempt (e.g. transcript type changed after a draft/paid
    // request exists). Discard local state; the parent is responsible for
    // cancelling the orphaned Transcript Request server-side if needed.
    state.value = "idle";
    feeAmount.value = null;
    requestName.value = null;
    paymentId.value = null;
    errorMessage.value = "";
    resumed.value = false;
    fetchPreview();
  }
);

const resumed = ref(false);

onMounted(async () => {
  await checkForResume();
  if (state.value === "idle") fetchPreview();
});

async function checkForResume() {
  // Payment can succeed and then the ticket-creation step never completes
  // (page reload, closed tab, lost connection right after Razorpay confirms).
  // Before offering a fresh Pay button, check whether the student already
  // has a paid-but-unsubmitted Transcript Request and resume it instead of
  // risking a second charge.
  try {
    const existing = await call("slcm.api.transcript_request.get_unlinked_paid_request");
    if (!existing) return;
    resumed.value = true;
    requestName.value = existing.name;
    feeAmount.value = existing.fee_amount;
    paymentRequired.value = Boolean(existing.payment_required);
    paymentId.value = existing.payment_reference || null;
    paidForType.value = existing.transcript_type;
    paidForCopies.value = existing.num_copies;
    state.value = "paid";
    emit("paid", {
      requestName: existing.name,
      feeAmount: existing.fee_amount,
      transcriptType: existing.transcript_type,
      numCopies: existing.num_copies,
      purpose: existing.purpose,
      deliveryMode: existing.delivery_mode,
    });
  } catch (e: any) {
    console.error("Transcript resume check failed", e);
  }
}

async function fetchPreview() {
  if (!props.transcriptType) return;
  try {
    const preview = await call(
      "slcm.api.transcript_request.get_fee_preview",
      { transcript_type: props.transcriptType, num_copies: props.numCopies || 1 }
    );
    feeAmount.value = preview.total;
    paymentRequired.value = Boolean(preview.payment_required);
  } catch (e: any) {
    // Fee preview is informational only — the ticket can still be created
    // via the fallback (pay-after) flow server-side if this fails.
    console.error("Transcript fee preview failed", e);
  }
}

async function startPayment() {
  errorMessage.value = "";
  state.value = "creating";
  try {
    const created = await call("slcm.api.transcript_request.create_request", {
      transcript_type: props.transcriptType,
      num_copies: props.numCopies || 1,
      purpose: props.purpose,
      delivery_mode: props.deliveryMode,
    });
    requestName.value = created.name;
    feeAmount.value = created.fee_amount;
    paymentRequired.value = Boolean(created.payment_required);
    paidForType.value = created.transcript_type;
    paidForCopies.value = created.num_copies;

    if (!paymentRequired.value) {
      state.value = "paid";
      emit("paid", {
        requestName: created.name,
        feeAmount: created.fee_amount,
        transcriptType: created.transcript_type,
        numCopies: created.num_copies,
      });
      return;
    }

    state.value = "ordering";
    const order = await call("slcm.api.transcript_request.initiate_payment", {
      request_name: created.name,
    });

    await loadRazorpayScript();
    openCheckout(order);
  } catch (e: any) {
    state.value = "error";
    errorMessage.value =
      e?.messages?.[0] || e?.message || __("Could not start payment. Please try again.");
  }
}

function loadRazorpayScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if ((window as any).Razorpay) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve();
    script.onerror = () =>
      reject(new Error(__("Could not load the payment gateway. Check your connection and try again.")));
    document.head.appendChild(script);
  });
}

function openCheckout(order: any) {
  state.value = "awaiting";
  const rzp = new (window as any).Razorpay({
    key: order.razorpay_key,
    amount: order.amount,
    currency: order.currency || "INR",
    order_id: order.razorpay_order_id,
    name: "Helpdesk",
    description: order.description || "Transcript Fee",
    prefill: order.prefill || {},
    theme: { color: "#171717" },
    handler: (resp: any) => confirmPayment(resp),
    modal: {
      ondismiss: () => {
        state.value = "idle";
        if (requestName.value) {
          call("slcm.api.transcript_request.cancel_payment_attempt", {
            request_name: requestName.value,
          }).catch(() => {});
        }
      },
    },
  });
  rzp.on("payment.failed", (resp: any) => {
    state.value = "error";
    errorMessage.value = resp?.error?.description || __("Payment failed. Please try again.");
    if (requestName.value) {
      call("slcm.api.transcript_request.record_payment_failure", {
        request_name: requestName.value,
        error_description: resp?.error?.description || "",
      }).catch(() => {});
    }
  });
  rzp.open();
}

async function confirmPayment(resp: any) {
  state.value = "verifying";
  try {
    const result = await call("slcm.api.transcript_request.confirm_payment", {
      request_name: requestName.value,
      razorpay_payment_id: resp.razorpay_payment_id,
      razorpay_order_id: resp.razorpay_order_id,
      razorpay_signature: resp.razorpay_signature,
    });
    if (!result?.success) {
      throw new Error(__("Payment verification failed."));
    }
    paymentId.value = resp.razorpay_payment_id;
    state.value = "paid";
    emit("paid", {
      requestName: requestName.value as string,
      feeAmount: feeAmount.value as number,
      transcriptType: paidForType.value as string,
      numCopies: paidForCopies.value as number | string,
    });
  } catch (e: any) {
    state.value = "error";
    errorMessage.value =
      e?.messages?.[0] ||
      e?.message ||
      __(
        "Payment received but verification failed. Contact support with payment ID: {0}",
        resp.razorpay_payment_id
      );
  }
}

defineExpose({
  isPaidOrNotRequired: computed(() => state.value === "paid"),
  paidRequestName: requestName,
});
</script>
