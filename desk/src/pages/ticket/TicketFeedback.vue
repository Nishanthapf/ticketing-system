<template>
  <Dialog
    :open="open"
    :title="__('Rate this ticket')"
    :actions="[
      {
        disabled: !preset,
        label: __('Submit'),
        theme: 'gray',
        variant: 'solid',
        onClick: () =>
          closeTicket.submit({
            feedback: preset,
            feedback_extra: text,
          }),
      },
    ]"
    @update:open="() => $emit('update:open', !open)"
  >
    <template #default>
      <div class="space-y-4 text-base text-ink-gray-7">
        <div class="space-y-2">
          <span> {{ __("Select a rating") }} </span>
          <span class="text-ink-red-3"> * </span>
          <StarRating
            :static="false"
            :rating="rating"
            v-model="rating"
            @update:model-value="rating = $event"
          />
        </div>
        <div v-if="options.data?.length" class="space-y-2">
          <span> {{ __("Pick an option") }} </span>
          <span class="text-ink-red-3"> * </span>
          <div class="flex flex-wrap gap-2">
            <Button
              v-for="o in options.data"
              :key="o.name"
              :label="o.label"
              :theme="preset === o.name ? 'blue' : 'gray'"
              variant="subtle"
              @click="preset = o.name"
            />
          </div>
        </div>
        <div class="space-y-2">
          <span> {{ __("Other") }} </span>
          <FormControl
            v-model="text"
            type="textarea"
            :placeholder="__('Tell us more')"
          />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { StarRating } from "@/components";
import { createListResource, createResource, toast } from "frappe-ui";
import { inject, ref, watch } from "vue";
import { ITicket } from "./symbols";
import { __ } from "@/translation";

interface P {
  open: boolean;
}

interface E {
  (event: "update:open", open: boolean): void;
}

defineProps<P>();
const emit = defineEmits<E>();
const ticket = inject(ITicket);
const rating = ref(0);
const text = ref("");
const preset = ref(null);
const options = createListResource({
  doctype: "HD Ticket Feedback Option",
  fields: ["name", "label"],
  pageLength: 99999,
});
const closeTicket = createResource({
  url: "helpdesk.helpdesk.doctype.hd_ticket.api.close_ticket_by_contact",
  debounce: 300,
  makeParams: (params) => ({
    name: ticket.data.name,
    feedback: params.feedback,
    feedback_extra: params.feedback_extra,
  }),
  onSuccess: () => {
    emit("update:open", false);
    ticket.reload();
  },
  onError: (err: any) => {
    ticket.reload();
    const msg =
      err?.messages?.[0] ||
      err?.message ||
      (typeof err === "string" ? err : err?.exc || __("Failed to update ticket"));
    toast.error(msg);
  },
});
watch(rating, (r) => {
  preset.value = null;
  text.value = "";
  options.update({
    filters: {
      rating: r,
      disabled: 0,
    },
  });
  options.reload();
});
</script>
