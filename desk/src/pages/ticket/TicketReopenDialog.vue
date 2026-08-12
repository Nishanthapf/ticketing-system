<template>
  <Dialog
    :open="open"
    :title="__('Reopen Ticket')"
    :actions="[
      {
        label: __('Reopen'),
        theme: 'gray',
        variant: 'solid',
        disabled: !reason.trim() || reopen.loading,
        loading: reopen.loading,
        onClick: () => reopen.submit(),
      },
    ]"
    @update:open="() => emit('update:open', !props.open)"
  >
    <template #default>
      <div class="space-y-2 text-base text-ink-gray-7">
        <span>{{ __("Why are you reopening this ticket?") }}</span>
        <span class="text-ink-red-3"> * </span>
        <FormControl
          v-model="reason"
          type="textarea"
          :placeholder="__('Tell us why this issue is not resolved')"
          autofocus
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { createResource, toast } from "frappe-ui";
import { inject, ref, watch } from "vue";
import { ITicket } from "./symbols";
import { __ } from "@/translation";

interface P {
  open: boolean;
}

interface E {
  (event: "update:open", open: boolean): void;
}

const props = defineProps<P>();
const emit = defineEmits<E>();
const ticket = inject(ITicket);
const reason = ref("");

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) reason.value = "";
  }
);

const reopen = createResource({
  url: "run_doc_method",
  makeParams: () => ({
    dt: "HD Ticket",
    dn: ticket.data.name,
    method: "reopen_ticket",
    args: { reason: reason.value.trim() },
  }),
  onSuccess: () => {
    toast.success(__("Ticket reopened."));
    emit("update:open", false);
    ticket.reload();
  },
  onError: (error: any) => {
    toast.error(error?.messages?.[0] || error?.message || __("Could not reopen ticket."));
  },
});
</script>
