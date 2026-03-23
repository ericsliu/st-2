"""YOLO class definitions for Uma Musume: Pretty Derby (Global/English client).

These 50 classes should match the labels used in your Label Studio annotation
project and in the YOLO training data under datasets/.

When training the model:
  - Class IDs must match these indices exactly
  - Use this file as the source of truth for both annotation and inference

Usage:
    from uma_trainer.perception.class_map import CLASS_NAMES, SCREEN_ANCHOR_CLASSES
"""

from uma_trainer.types import ScreenState, StatType, Mood

# Ordered list of class names (index = class ID in YOLO)
CLASS_NAMES: list[str] = [
    # 0–6: Action buttons
    "btn_confirm",
    "btn_cancel",
    "btn_train_speed",
    "btn_train_stamina",
    "btn_train_power",
    "btn_train_guts",
    "btn_train_wit",
    # 7–10: Training tile indicators
    "indicator_rainbow",
    "indicator_gold",
    "indicator_hint",
    "indicator_director",
    # 11–15: Mood icons
    "mood_great",
    "mood_good",
    "mood_normal",
    "mood_bad",
    "mood_terrible",
    # 16–21: Screen-state anchors (large/distinctive UI elements)
    "screen_training",
    "screen_event",
    "screen_race",
    "screen_skill_shop",
    "screen_result",
    "screen_loading",
    # 22–26: Stat display boxes
    "stat_box_speed",
    "stat_box_stamina",
    "stat_box_power",
    "stat_box_guts",
    "stat_box_wit",
    # 27: Energy bar
    "energy_bar",
    # 28–33: Support card slots on training tiles
    "support_card_slot_0",
    "support_card_slot_1",
    "support_card_slot_2",
    "support_card_slot_3",
    "support_card_slot_4",
    "support_card_slot_5",
    # 34–35: Race entry UI
    "btn_race_enter",
    "btn_race_skip",
    # 36–40: Skill shop UI
    "skill_card",
    "btn_buy_skill",
    "btn_skip_skills",
    "skill_cost_display",
    "skill_name_text",
    # 41–44: Event UI
    "event_popup",
    "event_choice_0",
    "event_choice_1",
    "event_choice_2",
    # 45–47: Career goal markers
    "goal_incomplete",
    "goal_complete",
    "goal_text",
    # 48–49: Misc
    "turn_counter",
    "btn_rest",
]

# Maps class name → class ID
CLASS_IDS: dict[str, int] = {name: idx for idx, name in enumerate(CLASS_NAMES)}

# Maps screen-anchor class name → ScreenState enum
SCREEN_ANCHOR_CLASSES: dict[str, ScreenState] = {
    "screen_training": ScreenState.TRAINING,
    "screen_event": ScreenState.EVENT,
    "screen_race": ScreenState.RACE,
    "screen_skill_shop": ScreenState.SKILL_SHOP,
    "screen_result": ScreenState.RESULT_SCREEN,
    "screen_loading": ScreenState.LOADING,
}

# Maps stat_box class name → StatType
STAT_BOX_TO_STAT: dict[str, StatType] = {
    "stat_box_speed": StatType.SPEED,
    "stat_box_stamina": StatType.STAMINA,
    "stat_box_power": StatType.POWER,
    "stat_box_guts": StatType.GUTS,
    "stat_box_wit": StatType.WIT,
}

# Maps training button class name → StatType
TRAIN_BTN_TO_STAT: dict[str, StatType] = {
    "btn_train_speed": StatType.SPEED,
    "btn_train_stamina": StatType.STAMINA,
    "btn_train_power": StatType.POWER,
    "btn_train_guts": StatType.GUTS,
    "btn_train_wit": StatType.WIT,
}

# Maps mood class name → Mood enum
MOOD_CLASS_TO_MOOD: dict[str, Mood] = {
    "mood_great": Mood.GREAT,
    "mood_good": Mood.GOOD,
    "mood_normal": Mood.NORMAL,
    "mood_bad": Mood.BAD,
    "mood_terrible": Mood.TERRIBLE,
}

NUM_CLASSES = len(CLASS_NAMES)
