/** Plain-English definitions for the numbers Courtside shows. `Term` shows one on hover; the Glossary page lists them all. */
export interface GlossaryEntry {
  term: string;
  def: string;
}

export const GLOSSARY_SECTIONS: { title: string; keys: TermKey[] }[] = [
  { title: "Players", keys: ["fp_pg", "value_pg", "fp_sd", "season", "last10", "mpg", "ros", "ros_games", "over_repl", "next7", "availability", "rostered", "today"] },
  { title: "Advanced stats", keys: ["xfp", "luck", "xfp_expected"] },
  { title: "How a projection is built", keys: ["source_weight", "games_pct", "preseason_blend"] },
  { title: "Your team", keys: ["pts_wk_team", "pts_wk_player", "vs_avg", "starts_wk", "confidence", "move_wk", "this_week", "move_ros", "ros_cost", "week_sd"] },
  { title: "Rankings and the season", keys: ["power", "proj_record", "playoffs", "title", "share", "starts_ros", "player_games_wk", "empty_slots", "bench_games", "pts_lost", "stat_diff"] },
  { title: "Matchups", keys: ["win_prob", "range80", "margin", "player_games", "tie", "sim_games", "sim_starts", "sim_pts", "swing"] },
  { title: "Trades", keys: ["verdict", "fit", "change_wk", "trade_ros", "playoff_weeks", "raw_value", "odds_change", "you_wk", "you_ros", "partner_pct", "accept"] },
];

export const GLOSSARY = {
  fp_pg: { term: "FP/G", def: "Fantasy points per game. Projected columns show what Courtside expects him to average from here on, under your league's scoring." },
  value_pg: { term: "Value/G", def: "In a categories league: how much a player helps across all your categories per game, on a fantasy-points scale so players can be compared." },
  fp_sd: { term: "± game", def: "How much his score swings from game to game. About two games in three land within this many points of his average: 30 ± 8 usually means 22 to 38." },
  season: { term: "Season", def: "What he has actually averaged this season so far." },
  last10: { term: "Last 10", def: "His average over his last 10 games: recent form. Early in the season it reaches back into last season's final games." },
  mpg: { term: "MPG", def: "Minutes per game. More minutes means more chances to score." },
  ros: { term: "ROS", def: "Rest of season: the fantasy points he's projected to score from today to the end of the season (points per game × the games he's expected to play)." },
  ros_games: { term: "ROS games", def: "Games he's expected to play for the rest of the season, after injuries and the games players typically miss." },
  over_repl: { term: "Over repl.", def: "Over replacement: how many more rest-of-season points he's worth than a player you could pick up from free agents. Zero or below means a free agent would do about as well." },
  next7: { term: "Next 7", def: "Games he's expected to play in the next 7 days, from his team's schedule and his injury status." },
  availability: { term: "Availability", def: "The share of his team's games he's expected to play, based on how often he has played in the past." },
  rostered: { term: "Rostered", def: "The share of ESPN leagues where he's on a team. A rough measure of how popular he is." },
  today: { term: "Today", def: "Fantasy points he has scored so far today. During games it refreshes every few minutes." },

  xfp: { term: "xFP/G", def: "Expected fantasy points per game this season: what his chances were worth, from NBA.com player tracking. Every shot is priced by where and how it was taken, and his passes, rebound chances, deflections and contests by how often he has converted them over the last two seasons." },
  luck: { term: "Luck", def: "Fantasy points per game this season minus xFP/G. Well above zero means he's scoring more than his chances were worth (a sell-high candidate); well below means less (a buy-low). The projection already expects part of the gap to close." },
  xfp_expected: { term: "Expected", def: "A stat as his chances were worth: made shots from the shots he took, assists from his potential assists, rebounds from his rebound chances, steals from deflections and blocks from contested shots." },

  source_weight: { term: "Weight", def: "How much each source counts in his preseason projection. The weights come from testing which mix predicted past seasons best." },
  games_pct: { term: "Games %", def: "The share of his team's games that source expects him to play." },
  preseason_blend: { term: "Preseason blend", def: "The sources combined: his projection before this season's games are added in. After 15 games this season it no longer counts." },

  pts_wk_team: { term: "Pts/wk", def: "Projected fantasy points per week, with the best possible lineup set every day." },
  pts_wk_player: { term: "Pts/wk", def: "The fantasy points per week this player adds to your lineup. Only games where he'd be in your starting lineup count." },
  vs_avg: { term: "vs avg", def: "Points per week above (+) or below (−) the average team in your league." },
  starts_wk: { term: "Starts/wk", def: "Games per week he's expected to be in your starting lineup rather than on the bench." },
  confidence: { term: "Confidence", def: "The chance this starter outscores the best bench player who could take his spot today. Low means it's close to a coin flip." },
  move_wk: { term: "Pts/wk (add/drop)", def: "How many points per week the add/drop adds to your lineup, with the best lineup set every day." },
  move_ros: { term: "Rest of season (add/drop)", def: "How many points the add/drop adds over the rest of the season." },
  this_week: { term: "This week", def: "How many points the move adds to your score in this week's matchup." },
  ros_cost: { term: "ROS cost", def: "Rest-of-season points you give up by dropping that player to make the move." },
  week_sd: { term: "± sd", def: "How far the weekly score typically lands from the projection. About two weeks in three are within this many points; the bar shows that range." },

  power: { term: "Power score", def: "A 0–100% rating of team strength. This season's record counts for the share of the season played; the rest is how often the team would beat every other team in a week (its all-play win rate) from its projections." },
  proj_record: { term: "Proj.", def: "Projected final record: wins so far plus the wins expected from simulating the rest of the season." },
  playoffs: { term: "Playoffs", def: "Chance to make the playoffs, from 5,000 simulated seasons." },
  title: { term: "Title", def: "Chance to win the championship, from 5,000 simulated seasons." },
  share: { term: "Share", def: "The share of the team's weekly fantasy points that comes from this player." },
  starts_ros: { term: "Starts", def: "Games he's expected to start (not sit on the bench) for this team for the rest of the season." },
  player_games_wk: { term: "Player-games per week", def: "How many games the team's players play in an average week. More games usually means more points." },
  empty_slots: { term: "Empty starting slots", def: "Lineup spots per week with nobody available to fill them. Fewer is better." },
  bench_games: { term: "Playable games on bench", def: "Games per week where a healthy player sits because the lineup is full: depth you can't use." },
  pts_lost: { term: "Pts lost", def: "Fantasy points the team loses while the player is out injured." },
  stat_diff: { term: "Diff", def: "Points per week this team gets from a stat, minus what the average team gets from it." },

  win_prob: { term: "Win", def: "Chance to win the matchup: the share of 10,000 simulated matchups the team wins. Each simulation plays out every game, with players having good and bad nights." },
  range80: { term: "80% range", def: "Where the score lands in 80% of the simulations. One time in 10 it's lower than the first number, one time in 10 higher than the second. A wide range means an unpredictable week." },
  margin: { term: "Expected margin", def: "The average gap between the two teams' scores across the simulations (first team minus second). Its 80% range shows how lopsided the matchup could get either way." },
  player_games: { term: "Player-games left", def: "Games each team's players have left this week. A team with more games has more chances to score." },
  tie: { term: "Tie", def: "Chance the matchup ends in a tie." },
  sim_games: { term: "G", def: "Games he's expected to play this week." },
  sim_starts: { term: "Starts", def: "Of those games, how many he's expected to be in the starting lineup." },
  sim_pts: { term: "Pts", def: "Fantasy points he's expected to score for the team this week (starts only)." },
  swing: { term: "Swing", def: "How much he can decide the matchup: how much more often the team wins when he has a big week (his best quarter of outcomes) than a quiet one (his worst quarter)." },

  verdict: { term: "Verdict", def: "Advantageous if the trade raises that team's rest-of-season points by 1% or more, disadvantageous if it lowers them by 1% or more, neutral in between." },
  fit: { term: "Fit", def: "How fair the trade is: win-win if every team gains, balanced if nobody gains or loses more than 1%, lose-lose if every team loses, otherwise who it favors." },
  change_wk: { term: "Change per week", def: "How many points per week the team's lineup gains or loses with the trade." },
  trade_ros: { term: "Rest of season", def: "The total change in the team's points for the rest of the season, and that as a % of everything it would have scored." },
  playoff_weeks: { term: "Fantasy playoff weeks", def: "The change in points during the fantasy playoff weeks only." },
  raw_value: { term: "Raw value change", def: "The change in the players' combined projected points, before checking who would actually start. The lineup-based numbers above are the ones that count." },
  odds_change: { term: "Playoff and title odds", def: "The team's chances before → after the trade, from simulating the rest of the season both ways." },
  you_wk: { term: "You/wk", def: "Points per week the trade adds to (or costs) your lineup." },
  you_ros: { term: "You ROS", def: "The change in your rest-of-season points, as a %." },
  partner_pct: { term: "Partner", def: "The change in the other team's rest-of-season points, as a %. Near zero or positive means they have a reason to accept." },
  accept: { term: "Chance they see it as a gain", def: "A rough chance the other manager's own numbers say yes: higher when the trade helps them more." },
} satisfies Record<string, GlossaryEntry>;

export type TermKey = keyof typeof GLOSSARY;
