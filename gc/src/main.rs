//! deaddemo-gc: Steam Game Coordinator helper for DeadDemo.
//!
//! Logs into Steam with the user's own account and asks the Deadlock GC (app 1422450) for the
//! replay salts of a match, exactly like the game client does when you press "Download replay".
//!
//! Contract (all secrets come from the environment, never argv; results go to stdout as JSON):
//!   deaddemo-gc login            DEADDEMO_STEAM_USER, DEADDEMO_STEAM_PASSWORD, [DEADDEMO_STEAM_GUARD_CODE]
//!                                -> {"refresh_token", "steam_id64", "account_id"}
//!   deaddemo-gc status           DEADDEMO_STEAM_USER, DEADDEMO_STEAM_REFRESH_TOKEN -> {"ok", "steam_id64"}
//!   deaddemo-gc salts <id>...    same env -> one line per match:
//!                                {"match_id","result","replay_salt","metadata_salt","cluster_id","replay_valid_through"}
//!   deaddemo-gc history <account_id> [max_pages]
//!                                same env -> one line per match (the client's own match history, newest
//!                                first, paginated with continue_cursor):
//!                                {"match_id","hero_id","start_time","match_duration_s","match_result",
//!                                 "player_team","player_kills","player_deaths","player_assists","last_hits",
//!                                 "denies","hero_level","net_worth","team_abandoned","abandoned_time_s",
//!                                 "match_mode","game_mode"}
//! Diagnostics go to stderr (RUST_LOG=debug for the wire-level log).

use std::env;
use std::io::Cursor;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use serde::Serialize;
use steam_vent::auth::{
    AuthConfirmationHandler, ConsoleAuthConfirmationHandler, DeviceConfirmationHandler, FileGuardDataStore,
    UserProvidedAuthConfirmationHandler,
};
use steam_vent::{Connection, ConnectionTrait, GameCoordinator, ServerList};
use steam_vent_proto_deadlock::citadel_gcmessages_client::{
    CMsgClientToGCGetMatchHistory, CMsgClientToGCGetMatchHistoryResponse, CMsgClientToGCGetMatchMetaData,
    CMsgClientToGCGetMatchMetaDataResponse,
};

const DEADLOCK_APP_ID: u32 = 1422450;
const STEAMID64_BASE: u64 = 76561197960265728;
const ENV_USER: &str = "DEADDEMO_STEAM_USER";
const ENV_PASSWORD: &str = "DEADDEMO_STEAM_PASSWORD";
const ENV_GUARD_CODE: &str = "DEADDEMO_STEAM_GUARD_CODE";
const ENV_TOKEN: &str = "DEADDEMO_STEAM_REFRESH_TOKEN";
const GC_TIMEOUT: Duration = Duration::from_secs(45);
// Valve rate-limits GetMatchMetaData per account; the deadlock-api ingest tool settled on 20 s.
const REQUEST_SPACING: Duration = Duration::from_secs(20);

#[derive(Serialize)]
struct LoginOut {
    refresh_token: String,
    steam_id64: u64,
    account_id: u32,
}

#[derive(Serialize)]
struct StatusOut {
    ok: bool,
    steam_id64: Option<u64>,
    error: Option<String>,
}

#[derive(Serialize)]
struct SaltsOut {
    match_id: u64,
    result: String,
    replay_salt: Option<u32>,
    metadata_salt: Option<u32>,
    cluster_id: Option<u32>,
    replay_valid_through: Option<u32>,
}

#[derive(Serialize)]
struct HistoryOut {
    match_id: u64,
    hero_id: Option<u32>,
    start_time: Option<u32>,
    match_duration_s: Option<u32>,
    match_result: Option<u32>,
    player_team: Option<i32>,
    player_kills: Option<u32>,
    player_deaths: Option<u32>,
    player_assists: Option<u32>,
    last_hits: Option<u32>,
    denies: Option<u32>,
    hero_level: Option<u32>,
    net_worth: Option<u32>,
    team_abandoned: Option<bool>,
    abandoned_time_s: Option<u32>,
    match_mode: Option<i32>,
    game_mode: Option<i32>,
}

fn env_required(name: &str) -> Result<String> {
    env::var(name)
        .ok()
        .filter(|v| !v.trim().is_empty())
        .ok_or_else(|| anyhow!("{name} is not set"))
}

fn print_json<T: Serialize>(value: &T) {
    println!("{}", serde_json::to_string(value).expect("serialize"));
}

async fn server_list() -> Result<ServerList> {
    ServerList::discover().await.context("could not discover Steam servers (offline?)")
}

/// Password login. A Steam Guard code from the environment is fed to steam-vent's user-input
/// handler through an in-memory reader; otherwise the terminal is asked, and in parallel the
/// mobile-app confirmation is awaited (whichever completes first wins).
async fn login_with_password(user: &str, password: &str, guard_code: Option<String>) -> Result<Connection> {
    let servers = server_list().await?;
    let guard_store = FileGuardDataStore::user_cache();
    let connection = match guard_code {
        Some(code) => {
            let input = Cursor::new(format!("{}\n", code.trim()).into_bytes());
            let handler =
                UserProvidedAuthConfirmationHandler::new(input, tokio::io::sink()).or(DeviceConfirmationHandler);
            Connection::login(&servers, user, password, guard_store, handler).await
        }
        None => {
            let handler = ConsoleAuthConfirmationHandler::default().or(DeviceConfirmationHandler);
            Connection::login(&servers, user, password, guard_store, handler).await
        }
    }
    .context("Steam login failed")?;
    Ok(connection)
}

async fn login_with_token(user: &str, token: &str) -> Result<Connection> {
    let servers = server_list().await?;
    Connection::access(&servers, user, token)
        .await
        .context("Steam rejected the stored token; log in again (deaddemo gc login)")
}

fn steam_ids(connection: &Connection) -> (u64, u32) {
    let id64: u64 = connection.steam_id().into();
    (id64, (id64 - STEAMID64_BASE) as u32)
}

async fn cmd_login() -> Result<()> {
    let user = env_required(ENV_USER)?;
    let password = env_required(ENV_PASSWORD)?;
    let guard = env::var(ENV_GUARD_CODE).ok().filter(|v| !v.trim().is_empty());
    let connection = login_with_password(&user, &password, guard).await?;
    let token = connection
        .access_token()
        .ok_or_else(|| anyhow!("login succeeded but Steam returned no token"))?
        .to_string();
    let (steam_id64, account_id) = steam_ids(&connection);
    eprintln!("logged in as {user} ({steam_id64})");
    print_json(&LoginOut { refresh_token: token, steam_id64, account_id });
    Ok(())
}

async fn connect_from_env() -> Result<Connection> {
    let user = env_required(ENV_USER)?;
    let token = env_required(ENV_TOKEN)?;
    let mut connection = login_with_token(&user, &token).await?;
    connection.set_timeout(GC_TIMEOUT);
    Ok(connection)
}

async fn cmd_status() -> Result<()> {
    match connect_from_env().await {
        Ok(connection) => {
            let (steam_id64, _) = steam_ids(&connection);
            print_json(&StatusOut { ok: true, steam_id64: Some(steam_id64), error: None });
        }
        Err(err) => {
            print_json(&StatusOut { ok: false, steam_id64: None, error: Some(format!("{err:#}")) });
        }
    }
    Ok(())
}

fn result_name(resp: &CMsgClientToGCGetMatchMetaDataResponse) -> String {
    // protobuf-rs derives Debug on the generated enum, giving the proto name (k_eResult_Success).
    match resp.result.as_ref() {
        Some(r) => match r.enum_value() {
            Ok(v) => format!("{v:?}"),
            Err(raw) => format!("unknown({raw})"),
        },
        None => "missing".to_string(),
    }
}

async fn fetch_one(gc: &GameCoordinator, match_id: u64) -> Result<SaltsOut> {
    let mut request = CMsgClientToGCGetMatchMetaData::new();
    request.set_match_id(match_id);
    // `job` tags the request with a job id and matches the reply on it, like the game client.
    let resp: CMsgClientToGCGetMatchMetaDataResponse =
        tokio::time::timeout(GC_TIMEOUT, gc.job(request))
            .await
            .map_err(|_| anyhow!("timed out waiting for the Game Coordinator"))?
            .context("GetMatchMetaData")?;
    let result = result_name(&resp);
    let success = resp.result.as_ref().map(|r| r.value() == 1).unwrap_or(false);
    let opt = |v: u32| if v == 0 { None } else { Some(v) };
    Ok(SaltsOut {
        match_id,
        result: if success { "success".to_string() } else { result },
        replay_salt: opt(resp.replay_salt()),
        metadata_salt: opt(resp.metadata_salt()),
        cluster_id: opt(resp.replay_group_id()),
        replay_valid_through: opt(resp.replay_valid_through()),
    })
}

async fn cmd_salts(ids: Vec<u64>) -> Result<()> {
    if ids.is_empty() {
        bail!("no match ids given");
    }
    let connection = connect_from_env().await?;
    let gc = game_coordinator(&connection).await?;
    let _ = DEADLOCK_APP_ID;
    let mut failures = 0usize;
    for (i, id) in ids.iter().enumerate() {
        if i > 0 {
            tokio::time::sleep(REQUEST_SPACING).await;
        }
        match fetch_one(&gc, *id).await {
            Ok(out) => {
                if out.result == "k_eResult_RateLimited" {
                    eprintln!("rate limited by the GC; stopping");
                    print_json(&out);
                    break;
                }
                print_json(&out);
            }
            Err(err) => {
                failures += 1;
                eprintln!("match {id}: {err:#}");
                print_json(&SaltsOut {
                    match_id: *id,
                    result: format!("error: {err:#}"),
                    replay_salt: None,
                    metadata_salt: None,
                    cluster_id: None,
                    replay_valid_through: None,
                });
            }
        }
    }
    if failures == ids.len() {
        bail!("every request failed");
    }
    Ok(())
}

async fn game_coordinator(connection: &Connection) -> Result<GameCoordinator> {
    // Announces "playing Deadlock" to Steam, then sends ClientHello until the GC answers Welcome.
    let (gc, _welcome) = tokio::time::timeout(
        GC_TIMEOUT,
        connection.game_coordinator(&steam_vent_proto_deadlock::GCHandshake::default()),
    )
    .await
    .map_err(|_| anyhow!("timed out waiting for the Deadlock Game Coordinator welcome"))?
    .context("Game Coordinator handshake")?;
    eprintln!("game coordinator ready");
    Ok(gc)
}

/// The client's own match history, straight from the GC (what the in-game history screen shows).
/// Pages are fetched newest-first until the GC returns no continue cursor or `max_pages` is reached.
async fn cmd_history(account_id: u32, max_pages: usize) -> Result<()> {
    let connection = connect_from_env().await?;
    let gc = game_coordinator(&connection).await?;
    let mut cursor: Option<u64> = None;
    let mut total = 0usize;
    for page in 0..max_pages.max(1) {
        if page > 0 {
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
        let mut request = CMsgClientToGCGetMatchHistory::new();
        request.set_account_id(account_id);
        if let Some(c) = cursor {
            request.set_continue_cursor(c);
        }
        let resp: CMsgClientToGCGetMatchHistoryResponse = tokio::time::timeout(GC_TIMEOUT, gc.job(request))
            .await
            .map_err(|_| anyhow!("timed out waiting for the Game Coordinator"))?
            .context("GetMatchHistory")?;
        let result = resp.result.as_ref().map(|r| match r.enum_value() {
            Ok(v) => format!("{v:?}"),
            Err(raw) => format!("unknown({raw})"),
        });
        eprintln!(
            "page {}: result={} matches={} cursor={:?}",
            page + 1,
            result.as_deref().unwrap_or("missing"),
            resp.matches.len(),
            resp.continue_cursor
        );
        if resp.matches.is_empty() {
            if total == 0 {
                bail!("GetMatchHistory returned no matches ({})", result.as_deref().unwrap_or("no result"));
            }
            break;
        }
        for m in &resp.matches {
            total += 1;
            print_json(&HistoryOut {
                match_id: m.match_id(),
                hero_id: m.hero_id,
                start_time: m.start_time,
                match_duration_s: m.match_duration_s,
                match_result: m.match_result,
                player_team: m.player_team.map(|t| t.value()),
                player_kills: m.player_kills,
                player_deaths: m.player_deaths,
                player_assists: m.player_assists,
                last_hits: m.last_hits,
                denies: m.denies,
                hero_level: m.hero_level,
                net_worth: m.net_worth,
                team_abandoned: m.team_abandoned,
                abandoned_time_s: m.abandoned_time_s,
                match_mode: m.match_mode.map(|v| v.value()),
                game_mode: m.game_mode.map(|v| v.value()),
            });
        }
        match resp.continue_cursor {
            Some(c) if c != 0 => cursor = Some(c),
            _ => break,
        }
    }
    eprintln!("{total} matches");
    Ok(())
}

fn usage() -> ! {
    eprintln!("usage: deaddemo-gc <login|status|salts <match_id>...|history <account_id> [max_pages]>");
    std::process::exit(2);
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .with_writer(std::io::stderr)
        .init();
    let mut args = env::args().skip(1);
    let command = args.next().unwrap_or_else(|| usage());
    let result = match command.as_str() {
        "login" => cmd_login().await,
        "status" => cmd_status().await,
        "salts" => {
            let ids: Result<Vec<u64>> = args.map(|a| a.parse::<u64>().context("match id must be a number")).collect();
            match ids {
                Ok(ids) => cmd_salts(ids).await,
                Err(e) => Err(e),
            }
        }
        "history" => {
            let account: Result<u32> =
                args.next().unwrap_or_else(|| usage()).parse::<u32>().context("account id must be a number");
            let pages = args.next().and_then(|p| p.parse::<usize>().ok()).unwrap_or(5);
            match account {
                Ok(account_id) => cmd_history(account_id, pages).await,
                Err(e) => Err(e),
            }
        }
        _ => usage(),
    };
    if let Err(err) = result {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}
