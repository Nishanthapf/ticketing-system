<template>
  <div class="mt-4 flex items-center justify-start gap-2.5">
    <Avatar :label="contact.data?.name" :image="contactImage" size="2xl" />
    <div class="flex flex-col gap-1.5">
      <Tooltip :text="contact.data?.name || contact.data?.email_id">
        <div class="flex gap-2 items-center flex-wrap">
          <p class="text-ink-gray-8 font-medium text-xl max-w-[150px] truncate">
            {{ contact.data?.name || contact.data?.email_id }}
          </p>
          <ExternalLinkIcon
            v-if="!contact.loading"
            class="size-4 text-ink-gray-6 cursor-pointer shrink-0"
            @click="openContact(contact.data.name)"
          />
          <!-- Status badge inline -->
          <Dropdown :options="statusDropdown" placement="right">
            <template #default>
              <button
                class="inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-base font-bold border-2 shadow-sm cursor-pointer transition-all hover:shadow-lg hover:scale-105"
                :class="statusBadgeClass"
              >
                <span class="size-2.5 rounded-full inline-block shrink-0" :class="statusDotClass"></span>
                {{ ticket.doc.status }}
              </button>
            </template>
          </Dropdown>
        </div>
      </Tooltip>
      <div class="flex gap-1.5" v-if="isCallingEnabled">
        <Tooltip :text="contact.data?.email_id">
          <!-- Email Button -->
          <Button size="sm" @click="toggleEmailBox()">
            <template #icon>
              <EmailIcon class="size-4" />
            </template>
          </Button>
          <!-- Call Button -->
          <Button size="sm" v-if="isCallingEnabled" @click="callContact">
            <template #icon>
              <PhoneIcon class="size-4" />
            </template>
          </Button>
        </Tooltip>
      </div>
    </div>
    <SetContactPhoneModal
      v-model="showPhoneModal"
      :name="contact.data?.name ?? ''"
      @onUpdate="contact.reload"
    />
  </div>
</template>

<script setup lang="ts">
import { toggleEmailBox } from "@/pages/ticket/modalStates";
import { useTelephonyStore } from "@/stores/telephony";
import { useUserStore } from "@/stores/user";
import { useTicketStatusStore } from "@/stores/ticketStatus";
import { ActivitiesSymbol, TicketContactSymbol, TicketSymbol } from "@/types";
import { HDTicketStatus } from "@/types/doctypes";
import { openContact } from "@/utils";
import { Avatar, Button, Dropdown, Tooltip } from "frappe-ui";
import { storeToRefs } from "pinia";
import { computed, h, inject, ref } from "vue";
import { ExternalLinkIcon, IndicatorIcon } from "../icons";
import EmailIcon from "../icons/EmailIcon.vue";
import PhoneIcon from "../icons/PhoneIcon.vue";
import SetContactPhoneModal from "../ticket/SetContactPhoneModal.vue";

const telephonyStore = useTelephonyStore();
const { getUser } = useUserStore();
const { isCallingEnabled } = storeToRefs(telephonyStore);
const showPhoneModal = ref(false);
const ticketStatusStore = useTicketStatusStore();

const ticket = inject(TicketSymbol)!;
const activities = inject(ActivitiesSymbol)!;

const statusDropdown = computed(() => {
  const statuses =
    ticketStatusStore.statuses.data?.filter((s: HDTicketStatus) => s.enabled) || [];
  return statuses.map((o: HDTicketStatus) => ({
    label: o.label_agent,
    value: o.label_agent,
    onClick: () => {
      if (ticket.value.doc.status === o.label_agent) return;
      ticket.value.setValue.submit(
        { status: o.label_agent },
        {
          onSuccess() {
            activities.value.reload();
          },
        }
      );
    },
    icon: () =>
      h(IndicatorIcon, {
        class: o.parsed_color,
      }),
  }));
});

const statusBadgeClass = computed(() => {
  const status = ticket.value.doc.status;
  const colorMap: Record<string, string> = {
    Open: "bg-green-100 text-green-800 border-green-400",
    Replied: "bg-blue-100 text-blue-800 border-blue-400",
    Resolved: "bg-gray-200 text-gray-700 border-gray-400",
    Closed: "bg-gray-200 text-gray-600 border-gray-400",
    "On Hold": "bg-orange-100 text-orange-800 border-orange-400",
  };
  return colorMap[status] ?? "bg-gray-100 text-gray-700 border-gray-300";
});

const statusDotClass = computed(() => {
  const status = ticket.value.doc.status;
  const dotMap: Record<string, string> = {
    Open: "bg-green-600",
    Replied: "bg-blue-600",
    Resolved: "bg-gray-500",
    Closed: "bg-gray-400",
    "On Hold": "bg-orange-500",
  };
  return dotMap[status] ?? "bg-gray-400";
});

const contact = inject(TicketContactSymbol)!;
const contactImage = computed(() => {
  if (!contact.value?.data) return "";
  const email = contact.value?.data?.email_id ?? "";
  return (
    contact.value?.data?.image || (email && getUser(email)?.user_image) || ""
  );
});

const callContact = () => {
  if (!contact.value.data.mobile_no && !contact.value.data.phone) {
    showPhoneModal.value = true;
    return;
  }
  telephonyStore.makeCall({
    number: contact.value.data.mobile_no || contact.value.data.phone,
    doctype: "HD Ticket",
    docname: ticket.value.name,
  });
};
</script>

<style scoped></style>
