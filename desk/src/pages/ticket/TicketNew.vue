<template>
  <div class="flex flex-col overflow-y-auto">
    <LayoutHeader>
      <template #left-header>
        <Breadcrumbs :items="breadcrumbs" />
      </template>
      <template #right-header>
        <CustomActions
          v-if="template.data?._customActions"
          :actions="template.data?._customActions"
        />
      </template>
    </LayoutHeader>
    <!-- Container -->
    <div
      class="flex flex-col gap-5 py-6 h-full flex-1 self-center overflow-auto mx-auto w-full max-w-4xl px-5"
    >
      <!-- custom fields descriptions -->
      <div v-if="Boolean(template.data?.about)" class="">
        <div class="prose-f" v-html="sanitize(template.data.about)" />
      </div>
      <!-- custom fields -->
      <div
        class="grid grid-cols-1 gap-4 sm:grid-cols-3"
        v-if="Boolean(visibleFields)"
      >
        <UniInput
          v-for="field in visibleFields"
          :key="field.fieldname"
          :field="field"
          :value="templateFields[field.fieldname]"
          @change="
            (e) => handleOnFieldChange(e, field.fieldname, field.fieldtype)
          "
        >
          <template v-if="field.fieldname === 'priority'" #label-extra>
            <template
              v-if="
                ticketPriorityResource.dataMap[templateFields[field.fieldname]]
                  ?.description
              "
            >
              <Tooltip
                :text="
                  ticketPriorityResource.dataMap[
                    templateFields[field.fieldname]
                  ].description.trim()
                "
              >
                <lucide-circle-question-mark class="h-4 w-4 text-ink-gray-6" />
              </Tooltip>
            </template>
          </template>
        </UniInput>
      </div>
      <!-- Transcript Request: fee + payment, must complete before Submit unlocks -->
      <TranscriptPaymentPanel
        v-if="requiresPrepayment"
        ref="paymentPanel"
        :transcript-type="templateFields.custom_transcript_type"
        :num-copies="templateFields.custom_transcript_num_copies"
        :purpose="templateFields.custom_transcript_purpose"
        :delivery-mode="templateFields.custom_transcript_delivery_mode"
        :reset-key="paymentResetKey"
        @paid="onTranscriptPaid"
      />

      <!-- existing fields -->
      <div class="flex flex-col gap-5">
        <div class="flex flex-col gap-2">
          <span class="block text-sm text-ink-gray-6">
            {{ __("Subject") }}
            <span class="place-self-center text-red-500"> * </span>
          </span>
          <FormControl
            v-model="subject"
            type="text"
            :placeholder="__('A short description')"
            maxlength="140"
          />
        </div>
        <SearchArticles
          v-if="isCustomerPortal"
          :query="subject"
          class="shadow"
        />
        <div v-if="isCustomerPortal">
          <TicketTextEditor
            ref="editor"
            v-model:attachments="attachments"
            v-model:content="description"
            :placeholder="__('Description')"
            expand
            :uploadFunction="(file:any)=>uploadFunction(file)"
          >
            <template #bottom-right>
              <Button
                :label="__('Submit')"
                theme="gray"
                variant="solid"
                :disabled="
                  $refs.editor.editor.isEmpty ||
                  ticket.loading ||
                  !subject ||
                  (requiresPrepayment && !transcriptPaid)
                "
                @click="() => ticket.submit()"
              />
            </template>
          </TicketTextEditor>
        </div>
      </div>

      <!-- for agent portal -->
      <div v-if="!isCustomerPortal">
        <TicketTextEditor
          ref="editor"
          v-model:attachments="attachments"
          v-model:content="description"
          :placeholder="__('Detailed explanation')"
          expand
        >
          <template #bottom-right>
            <Button
              :label="__('Submit')"
              theme="gray"
              variant="solid"
              :disabled="
                $refs.editor.editor.isEmpty ||
                ticket.loading ||
                !subject ||
                (requiresPrepayment && !transcriptPaid)
              "
              @click="() => ticket.submit()"
            />
          </template>
        </TicketTextEditor>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { LayoutHeader, UniInput } from "@/components";
import {
  handleLinkFieldUpdate,
  handleSelectFieldUpdate,
  parseField,
  setupCustomizations,
} from "@/composables/formCustomisation";
import { useAuthStore } from "@/stores/auth";
import { globalStore } from "@/stores/globalStore";
import { capture } from "@/telemetry";
import { __ } from "@/translation";
import { Field } from "@/types";
import { isCustomerPortal, uploadFunction } from "@/utils";
import {
  Breadcrumbs,
  Button,
  call,
  createListResource,
  createResource,
  FormControl,
  usePageMeta,
} from "frappe-ui";
import { useOnboarding } from "frappe-ui/frappe";
import sanitizeHtml from "sanitize-html";
import { computed, defineAsyncComponent, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import SearchArticles from "../../components/SearchArticles.vue";
import TranscriptPaymentPanel from "../../components/TranscriptPaymentPanel.vue";

const TicketTextEditor = defineAsyncComponent(
  () => import("./TicketTextEditor.vue")
);

interface P {
  templateId?: string;
}

const props = withDefaults(defineProps<P>(), {
  templateId: "",
});

const route = useRoute();
const router = useRouter();
const { $dialog } = globalStore();
const { updateOnboardingStep } = useOnboarding("helpdesk");
const { isManager, userId: userID } = useAuthStore();
const subject = ref("");
const description = ref("");
const attachments = ref([]);
const templateFields = reactive({});

// "Transcript Request" pays before the ticket exists (via TranscriptPaymentPanel,
// which creates the real Transcript Request + Razorpay payment using the same
// endpoints the student portal payment page uses), instead of the default
// create-ticket-then-pay-via-comment flow other ticket types use. Submit stays
// disabled until transcriptPaid is true.
const requiresPrepayment = computed(
  () => templateFields.ticket_type === "Transcript Request"
);
const transcriptPaid = ref(false);
const paidTranscriptRequest = ref<string | null>(null);
const paymentPanel = ref<InstanceType<typeof TranscriptPaymentPanel> | null>(null);
// Snapshot of (transcript type, copies) the current paid/resumed request was
// created against — used to tell a genuine student-initiated change apart
// from the auto-populate write the "Transcript Request" ticket_type
// onChange handler makes ~50ms after mount (see the NLS Student Ticket
// Auto-populate form script), which would otherwise look identical to the
// watcher below and wrongly discard a just-resumed paid request.
const paidForValues = ref<string | null>(null);
// Bumped to tell the payment panel to discard whatever request it already
// created/paid and start over, whenever an input the fee/eligibility
// depends on changes after a payment was already made for the old values.
const paymentResetKey = ref(0);

watch(
  () => [
    templateFields.custom_transcript_type,
    templateFields.custom_transcript_num_copies,
  ],
  ([type, copies]) => {
    if (!transcriptPaid.value) return;
    const current = JSON.stringify([type, copies]);
    if (current === paidForValues.value) return; // the resume/auto-populate write itself
    transcriptPaid.value = false;
    paidTranscriptRequest.value = null;
    paidForValues.value = null;
    paymentResetKey.value++;
  }
);

function onTranscriptPaid(payload: {
  requestName: string;
  feeAmount: number;
  transcriptType: string;
  numCopies: number | string;
  purpose?: string;
  deliveryMode?: string;
}) {
  transcriptPaid.value = true;
  paidTranscriptRequest.value = payload.requestName;
  // Sourced from the server response (what the paid request actually is),
  // not templateFields — avoids a race with the "Transcript Request"
  // onChange auto-populate that runs ~50ms after mount and could otherwise
  // make a just-resumed paid request look stale before it's even rendered.
  paidForValues.value = JSON.stringify([payload.transcriptType, payload.numCopies]);
  templateFields.custom_transcript_type = payload.transcriptType;
  templateFields.custom_transcript_num_copies = payload.numCopies;
  templateFields.custom_transcript_request = payload.requestName;
  // Only present when resuming an already-paid request — backfill the
  // fields so Subject/Submit validation isn't blocked by fields the student
  // filled in during a session that got interrupted after payment.
  if (payload.purpose) templateFields.custom_transcript_purpose = payload.purpose;
  if (payload.deliveryMode) templateFields.custom_transcript_delivery_mode = payload.deliveryMode;
}

const template = createResource({
  url: "helpdesk.helpdesk.doctype.hd_ticket_template.api.get_one",
  makeParams: () => ({
    name: props.templateId || "Default",
  }),
  auto: true,
  onSuccess: (data) => {
    description.value = data.description_template || "";
    oldFields = window.structuredClone(data.fields || []);
    setupCustomizations(template, {
      doc: templateFields,
      call,
      router,
      $dialog,
      applyFilters,
      setAbout: (html: string) => { template.data.about = html; },
    });
    setupTemplateFields(data.fields);
  },
});

function setupTemplateFields(fields) {
  fields.forEach((field: Field) => {
    templateFields[field.fieldname] = "";
  });
}

const ticketPriorityResource = createListResource({
  doctype: "HD Ticket Priority",
  fields: ["name", "description"],
  auto: true,
  cache: "ticketPriorities",
});

let oldFields = [];

function applyFilters(fieldname: string, filters: any = null) {
  const f: Field = template.data.fields.find((f) => f.fieldname === fieldname);
  if (!f) return;
  if (f.fieldtype === "Select") {
    handleSelectFieldUpdate(f, fieldname, filters, templateFields, oldFields);
  } else if (f.fieldtype === "Link") {
    handleLinkFieldUpdate(f, fieldname, filters, templateFields, oldFields);
  }
}

const customOnChange = computed(() => template.data?._customOnChange);

const visibleFields = computed(() => {
  let _fields = template.data?.fields?.filter(
    (f) => !isCustomerPortal.value || !f.hide_from_customer
  );
  if (!_fields) return [];
  return _fields.map((field) => parseField(field, templateFields));
});

function handleOnFieldChange(e: any, fieldname: string, fieldtype: string) {
  templateFields[fieldname] = e.value;
  const fieldDependentFns = customOnChange.value?.[fieldname];
  if (fieldDependentFns) {
    fieldDependentFns.forEach((fn: Function) => {
      fn(e.value, fieldtype);
    });
  }
}

const ticket = createResource({
  url: "helpdesk.helpdesk.doctype.hd_ticket.api.new",
  debounce: 300,
  makeParams: () => ({
    doc: {
      description: description.value,
      subject: subject.value,
      template: props.templateId,
      ...templateFields,
    },
    attachments: attachments.value,
  }),
  validate: (params) => {
    const fields = visibleFields.value?.filter((f) => f.required) || [];
    const toVerify = [...fields, "subject", "description"];
    for (const field of toVerify) {
      if (!params.doc[field.fieldname || field]) {
        return `${field.label || field} is required`;
      }
    }
  },
  onSuccess: (data) => {
    router.push({
      name: isCustomerPortal.value ? "TicketCustomer" : "TicketAgent",
      params: {
        ticketId: data.name,
      },
    });
    if (isManager) {
      updateOnboardingStep("create_first_ticket", true, false, () =>
        localStorage.setItem("firstTicket", data.name)
      );
    }
  },
});

function sanitize(html: string) {
  return sanitizeHtml(html, {
    allowedTags: sanitizeHtml.defaults.allowedTags.concat(["img"]),
  });
}

const breadcrumbs = computed(() => {
  const items = [
    {
      label: __("Tickets"),
      route: {
        name: isCustomerPortal.value ? "TicketsCustomer" : "TicketsAgent",
      },
    },
    {
      label: __("New Ticket"),
      route: {
        name: "TicketNew",
      },
    },
  ];
  return items;
});

usePageMeta(() => ({
  title: __("New Ticket"),
}));

onMounted(() => {
  capture("new_ticket_page", {
    data: {
      user: userID,
    },
  });
});
</script>
