"""Prompt templates for LLM queries."""

EVENT_DECISION_PROMPT = """You are an expert advisor for Uma Musume: Pretty Derby (Global/English).

Current in-game event:
{event}

Available choices:
{choices}

Current trainee stats:
{stats}

Turn: {turn}/{max_turns}
Scenario: {scenario}
Energy: {energy}
Mood: {mood}{advice_context}

Choose the option that best benefits long-term career progression. Prioritize:
1. Stat gains (especially for this trainee's primary stat)
2. Bond increases with support cards
3. Mood improvement if mood is Bad or Terrible
4. Avoid choices that reduce energy significantly if energy < 40
5. Follow the personal notes above if they are relevant to this event

Respond ONLY with valid JSON in this exact format:
{{"choice_index": 0, "reasoning": "Brief explanation", "confidence": 0.85}}

choice_index must be 0, 1, or 2 (whichever choices are available).
confidence should be 0.5-1.0 based on how certain you are.
"""

SKILL_BUILD_PROMPT = """You are an expert at Uma Musume: Pretty Derby skill selection.

Available skills to purchase:
{skills}

Current trainee stats:
{stats}

Turn: {turn}/{max_turns}
Scenario: {scenario}

Identify which skills are worth buying. Consider:
1. Speed/acceleration skills are highest priority for most trainers
2. Avoid overlapping skills that do the same thing
3. Recovery/healing skills are lower priority
4. Unique skills from hints are almost always worth buying

Respond ONLY with valid JSON:
{{"buy_ids": ["skill_id_1", "skill_id_2"], "reasoning": "Brief explanation"}}
"""

SUPPORT_CARD_EVAL_PROMPT = """You are an expert at Uma Musume: Pretty Derby support card evaluation.

Evaluate this support card for a {scenario} Career Mode run with {trainee_name}:

Card: {card_name} ({rarity})
Type: {card_type}
Training bonuses: {training_bonuses}
Bond skills: {bond_skills}

Current team composition: {team_composition}

Rate this card's synergy (1-10) and explain briefly.

Respond ONLY with valid JSON:
{{"synergy_score": 7, "reasoning": "Brief explanation", "slot_recommendation": "speed_slot"}}
"""

RUN_ANALYSIS_PROMPT = """Analyze this Uma Musume Career Mode run result:

Trainee: {trainee_id}
Scenario: {scenario}
Final stats: {final_stats}
Goals completed: {goals_completed}/{total_goals}
Turns taken: {turns_taken}

What could have been improved? Focus on stat distribution and skill selection.

Respond with plain text analysis (2-3 sentences).
"""
