-- Uma Trainer knowledge base schema
-- Applied via database.py on first run

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text_hash TEXT UNIQUE NOT NULL,      -- SHA256 of normalized event text
    event_text TEXT NOT NULL,
    character_id TEXT DEFAULT NULL,      -- NULL = generic event
    best_choice_index INTEGER NOT NULL DEFAULT 0,
    choice_effects TEXT NOT NULL DEFAULT '[]',  -- JSON array of effect descriptions
    source TEXT NOT NULL DEFAULT 'manual',       -- manual | llm | claude | scraper
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_hash ON events (text_hash);

CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_lower TEXT NOT NULL,            -- For case-insensitive search
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'unknown',     -- speed | stamina | recovery | support | unique
    stat_requirements TEXT DEFAULT '{}', -- JSON {stat: threshold}
    priority INTEGER NOT NULL DEFAULT 5, -- 1-10, higher = more desirable
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skills_name ON skills (name_lower);

CREATE TABLE IF NOT EXISTS support_cards (
    card_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT DEFAULT 'speed',           -- speed | stamina | power | guts | wit | friend
    rarity TEXT DEFAULT 'R',             -- SSR | SR | R
    tier INTEGER NOT NULL DEFAULT 3,     -- 1=S, 2=A, 3=B, 4=C
    bond_skills TEXT DEFAULT '[]',       -- JSON: [{bond_level, skill_id}]
    training_bonuses TEXT DEFAULT '{}',  -- JSON: {stat: bonus_value}
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS characters (
    character_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aptitudes TEXT DEFAULT '{}',         -- JSON: {stat: grade, surface: grade, distance: grade}
    scenario TEXT DEFAULT 'ura_finale',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS race_calendar (
    race_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    grade TEXT DEFAULT 'G3',             -- G1 | G2 | G3 | OP | Pre-OP
    distance INTEGER DEFAULT 1600,
    surface TEXT DEFAULT 'turf',         -- turf | dirt
    direction TEXT DEFAULT 'right',      -- right | left
    season TEXT DEFAULT 'spring',        -- spring | summer | fall | winter
    year INTEGER DEFAULT 1,              -- 1 | 2 | 3 (in-game year)
    fan_reward INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY,          -- SHA256(model + input)
    response TEXT NOT NULL,              -- JSON response
    model TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_cache (expires_at);

CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT PRIMARY KEY,
    trainee_id TEXT DEFAULT '',
    scenario TEXT DEFAULT 'ura_finale',
    final_stats TEXT DEFAULT '{}',       -- JSON TraineeStats
    goals_completed INTEGER DEFAULT 0,
    total_goals INTEGER DEFAULT 0,
    turns_taken INTEGER DEFAULT 0,
    success INTEGER DEFAULT 0,           -- 0 | 1
    notes TEXT DEFAULT '',
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);
