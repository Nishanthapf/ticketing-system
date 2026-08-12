<template>
  <div class="flex flex-col gap-3">
    <div class="flex items-center justify-between gap-2 cursor-pointer">
      <div class="flex flex-col gap-1">
        <span class="text-sm text-ink-gray-8 font-medium">
          {{ __("Enable Ticket Escalation") }}
        </span>
        <span class="text-p-sm text-ink-gray-5">
          {{
            __(
              "Automatically escalate tickets through the levels below if an agent does not reply in time."
            )
          }}
        </span>
      </div>
      <Switch v-model="escalationEnabled" />
    </div>

    <div v-if="escalationEnabled" class="flex flex-col gap-3">
      <div class="flex items-center justify-between gap-2">
        <span class="text-sm text-ink-gray-7">{{ __("Number of Escalation Levels") }}</span>
        <Select
          class="w-24"
          :options="levelCountOptions"
          :modelValue="String(levels.length || 1)"
          @update:modelValue="setLevelCount"
        />
      </div>

      <div
        class="rounded-md border p-1 border-outline-gray-2 text-sm"
        v-if="levels.length"
      >
        <div
          class="grid gap-2 p-2 items-center text-ink-gray-5"
          :style="{ gridTemplateColumns: gridTemplate }"
        >
          <div>{{ __("Level") }}</div>
          <div>{{ __("Assignee") }}</div>
          <div>{{ __("Escalate After (hrs)") }}</div>
          <div>{{ __("Access") }}</div>
          <div class="text-center">{{ __("Notify") }}</div>
          <div></div>
        </div>
        <hr class="my-0.5" />
        <div v-for="(level, index) in levels" :key="index">
          <div
            class="grid gap-2 px-2 py-2 items-center"
            :style="{ gridTemplateColumns: gridTemplate }"
          >
            <div class="text-ink-gray-7 font-medium">
              {{ __("Level {0}", [index + 1]) }}
            </div>
            <Select
              :options="agentOptions"
              v-model="level.assigned_to"
              placeholder="Select agent"
            />
            <FormControl
              type="number"
              :min="0"
              step="0.5"
              v-model="level.escalate_after_hours"
            />
            <Select :options="accessLevelOptions" v-model="level.access_level" />
            <div class="flex justify-center">
              <Checkbox v-model="level.notify_assignee" />
            </div>
            <div class="flex justify-end">
              <Button
                icon="lucide-trash-2"
                variant="ghost"
                @click="removeLevel(index)"
              />
            </div>
          </div>
          <hr class="my-0.5" v-if="index !== levels.length - 1" />
        </div>
      </div>
      <div v-else class="text-center p-4 text-ink-gray-5 border rounded-md">
        {{ __("No escalation levels configured yet.") }}
      </div>

      <Button
        variant="subtle"
        @click="addLevel"
        :disabled="levels.length >= maxLevels"
        :label="__('Add Escalation Level')"
        icon-left="lucide-plus"
      />
      <div v-if="levels.length >= maxLevels" class="text-p-sm text-ink-gray-5">
        {{ __("A maximum of {0} escalation levels is allowed.", [maxLevels]) }}
      </div>

      <div class="flex justify-end">
        <Button
          variant="solid"
          :label="__('Save Escalation Levels')"
          @click="saveLevels"
        />
      </div>
    </div>
  </div>

  <ConfirmDialog
    v-if="shrinkConfirm.show"
    :title="__('Remove Escalation Levels')"
    :message="shrinkConfirm.message"
    @confirm="shrinkConfirm.onConfirm"
    @cancel="shrinkConfirm.onCancel"
  />
</template>

<script setup lang="ts">
import ConfirmDialog from "@/components/ConfirmDialog.vue";
import { useAgentStore } from "@/stores/agent";
import { __ } from "@/translation";
import { Button, Checkbox, FormControl, Select, Switch, toast } from "frappe-ui";
import { storeToRefs } from "pinia";
import { computed, ref, watch } from "vue";

const props = defineProps<{
  team: any;
}>();

const maxLevels = 6;
const gridTemplate = "5rem 1fr 8rem 8rem 4rem 2.5rem";

const { agents } = storeToRefs(useAgentStore());

const accessLevelOptions = [
  { label: __("Read Only"), value: "Read Only" },
  { label: __("Reply Only"), value: "Reply Only" },
  { label: __("Read & Reply"), value: "Read & Reply" },
  { label: __("Full Access"), value: "Full Access" },
];

const levelCountOptions: { label: string; value: string }[] = Array.from(
  { length: maxLevels },
  (_, i) => ({
    label: String(i + 1),
    value: String(i + 1),
  })
);

const agentOptions = computed(() =>
  (agents.value.data || []).map((a: any) => ({
    label: a.agent_name,
    value: a.user,
  }))
);

const escalationEnabled = computed({
  get() {
    return !!props.team.doc?.enable_ticket_escalation;
  },
  set(value: boolean) {
    if (!props.team.doc) return;
    props.team.setValue.submit(
      { enable_ticket_escalation: value },
      {
        onSuccess: () => props.team.reload(),
      }
    );
  },
});

const levels = ref<any[]>([]);

function syncLevelsFromDoc() {
  levels.value = (props.team.doc?.escalation_levels || []).map((row: any) => ({
    ...row,
  }));
}

watch(
  () => props.team.doc?.escalation_levels,
  () => syncLevelsFromDoc(),
  { immediate: true, deep: true }
);

function addLevel() {
  if (levels.value.length >= maxLevels) return;
  levels.value.push({
    assigned_to: "",
    escalate_after_hours: 24,
    access_level: "Read & Reply",
    notify_assignee: 1,
  });
}

function removeLevel(index: number) {
  levels.value.splice(index, 1);
}

function blankLevel() {
  return {
    assigned_to: "",
    escalate_after_hours: 24,
    access_level: "Read & Reply",
    notify_assignee: 1,
  };
}

const shrinkConfirm = ref<{
  show: boolean;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}>({
  show: false,
  message: "",
  onConfirm: () => {},
  onCancel: () => {},
});

function setLevelCount(value: string | number | undefined) {
  if (value === undefined) return;
  const target = Math.min(Math.max(parseInt(String(value), 10) || 1, 1), maxLevels);
  const current = levels.value.length;

  if (target === current) {
    return;
  }

  if (target > current) {
    for (let i = current; i < target; i++) {
      levels.value.push(blankLevel());
    }
    return;
  }

  // Shrinking — confirm before dropping rows that already have configuration,
  // so an admin can't lose a configured level by accident.
  const removed = levels.value.slice(target);
  const hasData = removed.some((r) => r.assigned_to || r.escalate_after_hours);

  if (!hasData) {
    levels.value = levels.value.slice(0, target);
    return;
  }

  shrinkConfirm.value = {
    show: true,
    message: __(
      "This will remove {0} configured escalation level(s) (Level {1} onward). Continue?",
      [removed.length, target + 1]
    ),
    onConfirm: () => {
      levels.value = levels.value.slice(0, target);
      shrinkConfirm.value.show = false;
    },
    onCancel: () => {
      shrinkConfirm.value.show = false;
    },
  };
}

function saveLevels() {
  for (const [i, level] of levels.value.entries()) {
    if (!level.assigned_to) {
      toast.error(__("Row #{0}: Assignee is required.", [i + 1]));
      return;
    }
    if (!level.escalate_after_hours || Number(level.escalate_after_hours) <= 0) {
      toast.error(
        __("Row #{0}: Escalate After (hours) must be greater than 0.", [i + 1])
      );
      return;
    }
  }

  props.team.setValue.submit(
    { escalation_levels: levels.value },
    {
      onSuccess: () => {
        toast.success(__("Escalation levels saved."));
        props.team.reload();
      },
    }
  );
}
</script>
