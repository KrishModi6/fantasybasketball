export interface User {
  id: string;
  email: string;
  display_name: string;
}

export interface LeagueSummary {
  id: string;
  name: string;
  season: number;
  source: "espn" | "demo" | "file" | "flaim";
  role: "owner" | "member";
  team_id: number | null;
  member_count: number;
  team_count: number | null; // null before the first sync
  sync_status: string;
  last_synced_at: string | null;
}

export interface TeamRef {
  id: number;
  name: string;
  abbrev: string;
  owner_name: string | null;
  wins: number;
  losses: number;
  ties: number;
  points_for: number;
  points_against: number;
  claimed_by: string | null;
}

export interface Scoring {
  kind: "points" | "categories";
  points: Record<string, number>;
  categories: string[];
}

export interface League {
  id: string;
  name: string;
  season: number;
  source: LeagueSummary["source"];
  sync: { status: string; error: string | null; last_synced_at: string | null; interval_hours: number };
  connector: { espn_league_id: string | null; has_cookies: boolean | null; shared_by: string | null };
  me: { role: "owner" | "member"; team_id: number | null } | null;
  member_count: number;
  teams: TeamRef[];
  settings: {
    scoring: Scoring;
    roster_slots: Record<string, number>;
    team_count: number;
    regular_season_matchups: number;
    playoff_teams: number;
    playoff_round_weeks: number[];
    acquisition_limit_per_matchup: number | null;
    trade_deadline: string | null;
    lineup_lock: string;
    current_matchup_period: number;
    current_period: number;
    today: string | null;
    live_window: [string, string] | null; // today's first tip-off until the last game should be over
    matchup_periods: Record<string, [string | null, string | null]>;
  } | null;
}

export interface PlayerRow {
  id: string;
  name: string;
  pro_team: string;
  positions: string[];
  eligible_slots: string[];
  injury_status: string;
  injury_note: string | null;
  fantasy_team_id: number | null;
  on_waivers: boolean;
  age: number | null;
  ownership_pct: number | null;
  fp_pg: number;
  fp_sd: number;
  preseason_fp_pg: number;
  season_fp_pg: number | null;
  recent_fp_pg: number | null;
  today_fp: number | null; // today's game so far, once he has played in it
  today_min: number | null;
  games_played: number;
  mpg: number;
  availability: number;
  games_out: number;
  ros_games: number;
  ros_value: number;
  vorp: number;
  value_pg: number;
  rank: number;
  games_next7: number;
  per_game: Record<string, number>;
  fg_pct: number | null;
  ft_pct: number | null;
  flags: string[];
  gscores?: Record<string, number>;
  // team roster extras
  slot?: string;
  exp_starts?: number;
  exp_games?: number;
  pts_per_week?: number;
}

export interface SourceEstimate {
  source: string;
  weight: number;
  mpg: number;
  availability: number | null;
  fp_pg: number;
  per_game: Record<string, number>;
}
export interface Adjustment {
  kind: string;
  label: string;
  detail: string;
  minutes_pct: number;
  games_out: number;
}

export interface PlayerDetail extends PlayerRow {
  sources: SourceEstimate[];
  adjustments: Adjustment[];
  explanation: { factor: string; fp_pg: number }[];
  in_season_weight: number;
  carryover_games: number;
  carryover_weight: number;
  fp_breakdown: Record<string, number>;
  game_log: { season: number; period: number; date: string | null; team: string; min: number; fp: number; [k: string]: number | string | null }[];
  seasons: { season: number; label: string; team: string; gp: number; mpg: number | null; fp_pg: number | null; [k: string]: number | string | null }[];
  this_season: PlayerDetail["seasons"][number] | null; // this season's per-game line (with MIN), once he has played
  news: NewsItem[];
  trend: { as_of: string; fp_pg: number; mpg: number; games_out: number }[];
  override: { minutes_pct: number; out_games: number | null; note: string } | null;
}

export interface NewsItem {
  id: string;
  headline: string;
  description: string;
  published: string | null;
  link: string | null;
  players?: { id: string; name: string; fantasy_team_id: number | null }[];
}

export interface TeamStrength {
  team_id: number;
  name: string;
  abbrev: string;
  owner: string | null;
  rank: number;
  power_score: number;
  projected_allplay: number;
  record_pct: number | null;
  record_weight: number;
  ppw: number;
  ppw_vs_avg: number;
  wins: number;
  losses: number;
  ties: number;
  proj_wins: number;
  proj_losses: number;
  playoff_odds: number;
  title_odds: number;
  avg_seed: number;
  current: { matchup_period: number; opponent_id: number; proj_score: number; opp_proj_score: number; win_prob: number } | null;
  explanation: {
    summary: string;
    stat_breakdown?: { stat: string; pts_per_week: number; league_avg: number; diff: number }[];
    category_win_prob?: { category: string; win_prob: number }[];
    players: { player_id: string; name: string; per_week: number; share: number; starts: number; games: number }[];
    depth: { empty_slots_per_week: number; league_avg_empty: number; bench_games_per_week: number; league_avg_bench: number };
    injuries: { player_id: string; name: string; status: string; games_out: number; fp_lost: number }[];
    schedule: { player_games_per_week: number; league_avg: number };
  };
}

export interface WeeklyPoints {
  scoring: string;
  weeks: { matchup_period: number; start: string | null; end: string | null; days: number; playoffs: boolean; current: boolean }[];
  teams: {
    team_id: number;
    name: string;
    abbrev: string;
    owner: string | null;
    weeks: { matchup_period: number; proj: number; sd: number; scored: number | null; opponent_id: number | null; win_prob: number | null }[];
  }[];
  as_of: { date: string | null };
}

export interface TeamDetail {
  id: number;
  name: string;
  abbrev: string;
  owner_name: string | null;
  record: { wins: number; losses: number; ties: number; points_for: number; points_against: number };
  roster: PlayerRow[];
  strength: TeamStrength | null;
  upcoming: { matchup_period: number; start: string | null; end: string | null; proj_mean: number; proj_sd: number; opponent_id: number | null; playoffs: boolean }[];
  ros_total: number;
  weeks_remaining: number;
}

export interface TeamReport {
  weaknesses: { kind: string; severity: number; title: string; detail: string; player_id?: string }[];
  moves: { add: string; add_name: string; drop: string; drop_name: string; delta_ros: number; delta_per_week: number; delta_this_week: number; reasons: string[]; on_waivers: boolean }[];
  streaming: { days_left: number; acquisition_limit?: number | null; moves: { add: string; add_name: string; drop: string; drop_name: string; games_this_week: number; delta_this_week: number; ros_cost: number }[] };
}

export interface LineupDay {
  period: number;
  date: string | null;
  expected: number;
  starters: { player_id: string; name: string; slot: string; projected: number; confidence: number; alternative: string | null }[];
  bench: { player_id: string; name: string; projected: number }[];
  no_game: { player_id: string; name: string }[];
}

export interface TradeSide {
  team_id: number;
  team_name: string;
  gives: string[];
  receives: string[];
  auto_drops: string[];
  auto_adds: string[];
  open_spot_adds: string[]; // free agents the team's open spots get, with or without the trade
  counted_as_pickups: [string, string][]; // [player, free agent]: traded away or dropped, valued as that pickup
  before_ros: number;
  after_ros: number;
  delta_ros: number;
  delta_pct: number;
  delta_per_week: number;
  delta_this_week: number;
  delta_playoffs: number;
  raw_value_delta: number;
  before_playoff_odds: number | null;
  after_playoff_odds: number | null;
  before_title_odds: number | null;
  after_title_odds: number | null;
  verdict: "advantageous" | "neutral" | "disadvantageous";
}

export interface TradeMove {
  player_id: string;
  from_team: number;
  to_team: number;
  name?: string; // set by the three-team finder
}

export interface TradeEval {
  /** Two teams, or three for a three-team trade; sides are in the same order. */
  teams?: number[];
  moves?: TradeMove[];
  sides: TradeSide[];
  players: {
    player_id: string;
    name: string;
    pro_team: string;
    positions: string[];
    fantasy_team_id: number | null;
    fp_pg: number;
    fp_sd: number;
    ros_games: number;
    ros_points: number;
    vorp: number;
    injury_status: string;
    games_out: number;
    season_fp_pg: number | null;
    recent_fp_pg: number | null;
  }[];
  math: { team_id: number; team_name: string; lines: string[] }[];
  summary: string;
  fairness: { label: string; partner_accept_likelihood: number };
}

export interface TradeSuggestion {
  partner_id: number;
  partner_name: string;
  give: string[];
  get: string[];
  give_names: string[];
  get_names: string[];
  my_delta_ros: number;
  my_delta_per_week: number;
  my_delta_pct: number;
  partner_delta_ros: number;
  partner_delta_pct: number;
  fairness: { label: string; partner_accept_likelihood: number };
  summary: string;
}

/** A row of the offer and ask finders: a whole two-team trade, one side of which was picked. */
export type TradeOffer = TradeSuggestion;

/** What to offer for players on another team: offers the partner accepts, else the closest refusals. */
export interface TradeOffers {
  partner_id: number;
  partner_name: string;
  get: string[];
  get_names: string[];
  offers: TradeOffer[];
  closest: TradeOffer[];
  checked: Record<string, number>;
}

/** What to ask for your players from a team that wants them (partner_id null: from every team). */
export interface TradeAsks {
  partner_id: number | null;
  partner_name: string | null;
  give: string[];
  give_names: string[];
  offers: TradeOffer[];
  closest: TradeOffer[];
  checked: Record<string, number>;
}

/** A three-team rotation: you send a player to one team, it sends one to the third, and that one sends one back. */
export interface ThreeTeamSuggestion {
  teams: number[]; // you first, then the team you send to, then the team you get from
  moves: TradeMove[];
  give: string[];
  get: string[];
  give_names: string[];
  get_names: string[];
  my_delta_ros: number;
  my_delta_per_week: number;
  my_delta_pct: number;
  partners: { team_id: number; team_name: string; delta_ros: number; delta_pct: number }[];
  fairness: { label: string; partner_accept_likelihood: number };
  summary: string;
}

export interface Member {
  user_id: string;
  display_name: string;
  role: "owner" | "member";
  team_id: number | null;
  joined_at: string;
}

export interface Invite {
  code: string;
  url: string;
  uses: number;
  max_uses: number | null;
  expires_at: string | null;
  revoked: boolean;
  created_at: string;
}

export interface ActivityItem {
  id: number;
  kind: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ModelCard {
  trained: boolean;
  trained_at?: string;
  trained_through?: number;
  train_rows?: number;
  scoring?: string;
  report_seasons?: number[];
  espn_report_seasons?: number[];
  summary_all?: Record<string, { fppg_mae: number; fppg_rmse: number; total_mae: number; total_spearman: number; fppg_spearman: number }>;
  data_audit?: { excluded_espn_seasons: number[]; adp_missing_seasons: number[]; espn_by_season: Record<string, { mean_gp: number; fppg_mae: number | null }> };
  summary?: Record<string, { fppg_mae: number; fppg_rmse: number; total_mae: number; total_spearman: number; fppg_spearman: number }>;
  by_season?: Record<string, { n_players: number; methods: Record<string, { fppg_mae: number; total_spearman: number }> }>;
  ensemble_weights?: Record<string, Record<string, number>>;
  prior_minutes?: Record<string, { low: number; high: number; split: number }>;
  overdispersion?: Record<string, number>;
  fp_game_cv?: number;
  feature_importance?: Record<string, [string, number][]>;
}

export interface WeekOption {
  matchup_period: number;
  start: string | null;
  end: string | null;
  is_current: boolean;
  is_playoff: boolean;
}

export interface WeekMatchup {
  home_id: number;
  away_id: number;
  home_name: string;
  away_name: string;
  home_proj: number;
  away_proj: number;
  home_sd: number;
  away_sd: number;
  home_score: number;
  away_score: number;
  home_win_prob: number;
}

export interface SimPlayer {
  player_id: string;
  name: string;
  pro_team: string;
  positions: string[];
  injury_status: string;
  excluded: boolean;
  games: number;
  starts: number;
  mean: number;
  p10: number;
  p90: number;
  swing: number;
}

export interface SimTeam {
  team_id: number;
  name: string;
  abbrev: string;
  owner: string | null;
  win_prob: number;
  accrued: number | null;
  mean: number;
  sd: number;
  p10: number;
  p25: number;
  median: number;
  p75: number;
  p90: number;
  hist: number[];
  games: number;
  starts: number;
  players: SimPlayer[];
  by_day: { period: number; date: string | null; mean: number; games: number; starts: number }[];
}

export interface SimResult {
  matchup_period: number;
  is_current: boolean;
  is_playoff: boolean;
  days_simulated: number;
  start: string | null;
  end: string | null;
  sims: number;
  scoring: "points" | "categories";
  tie_prob: number;
  excluded: string[];
  bins: number[];
  teams: [SimTeam, SimTeam];
  margin: { mean: number; p10: number; p90: number; bins: number[]; hist: number[] };
  categories?: {
    rows: { category: string; a_mean: number; b_mean: number; a_win: number; b_win: number; tie: number }[];
    a_cats_won_dist: number[];
    a_cats_mean: number;
    b_cats_mean: number;
  };
}

export interface AiStatus {
  mode: "direct" | "proxy" | "off";
  enabled: boolean;
  model: string;
  presets: string[];
  own_key: boolean;
  own_key_hint: string | null;
  shared: boolean;
}

export interface AiAnswer {
  answer: string;
  response_id: string;
  tools_used: { name: string; args: Record<string, unknown>; ok: boolean }[];
  seconds: number;
  quota: { device_used: number; device_limit: number; resets_at: string } | null;
}
