package dev.friendlyfire.ffwhitelistsync;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.velocitypowered.api.event.Subscribe;
import com.velocitypowered.api.event.connection.LoginEvent;
import com.velocitypowered.api.event.player.PlayerChooseInitialServerEvent;
import com.velocitypowered.api.event.proxy.ProxyInitializeEvent;
import com.velocitypowered.api.event.proxy.ProxyShutdownEvent;
import com.velocitypowered.api.plugin.Plugin;
import com.velocitypowered.api.plugin.annotation.DataDirectory;
import com.velocitypowered.api.proxy.Player;
import com.velocitypowered.api.proxy.ProxyServer;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.slf4j.Logger;

import jakarta.inject.Inject;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.HashSet;
import java.util.Properties;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Pull-based whitelist bridge: periodically fetches the authoritative
 * player list (uuid+name pairs) from the FriendlyFire account service,
 * swaps the in-memory allow-set, mirrors it to vanilla-format
 * whitelist.json (atomic replace) and enforces it on every login.
 *
 * Design notes:
 * - The in-memory set starts from the local whitelist.json cache so the
 *   proxy is protected even before/outside the first successful fetch.
 * - Enforcing happens HERE (LoginEvent denial); the vanilla/plugin
 *   whitelist mechanisms on the proxy are therefore switched off to
 *   avoid two competing judges.
 * - Fail-closed by design: whoever is not in the mirror cannot join.
 */
@Plugin(id = "ffwhitelistsync",
        name = "FfWhitelistSync",
        version = "1.1.0",
        description = "Authoritative whitelist pulled from the FriendlyFire account service",
        authors = {"FriendlyFire"})
public final class FfWhitelistSync {

    private final ProxyServer proxy;
    private final Logger logger;
    private final Path dataDir;

    /** Immutable snapshot, swapped wholesale by the updater thread. */
    private volatile Set<String> allowedUuids = Set.of();
    private volatile long lastAppliedRevision = -1;
    private volatile String lastMirrorSignature = "";

    private ScheduledExecutorService scheduler;
    private HttpClient http;
    private URI feedUri;
    private String authToken;
    private Duration interval;
    private Path whitelistJson;

    /** Persistent uuid -> last-server map backing the join-routing memory. */
    private Path lastServersJson;

    @Inject
    public FfWhitelistSync(ProxyServer proxy, Logger logger,
                           @DataDirectory Path dataDir) {
        this.proxy = proxy;
        this.logger = logger;
        this.dataDir = dataDir;
    }

    @Subscribe
    public void onProxyInitialization(ProxyInitializeEvent event) {
        try {
            loadConfiguration();
        } catch (IOException e) {
            logger.error("configuration unreadable, disabling sync", e);
            return;
        }

        preloadLocalCache();
        loadLastServers();

        http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();

        scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "ff-whitelist-sync");
            t.setDaemon(true);
            return t;
        });

        long firstDelay = 5; // seconds after proxy boot
        long period = Math.max(15, interval.getSeconds());
        scheduler.scheduleWithFixedDelay(this::safeRefresh,
                firstDelay, period, TimeUnit.SECONDS);
        // presence heartbeat: keep the website's live-player list fresh
        scheduler.scheduleWithFixedDelay(this::safeReportPresence,
                8, 20, TimeUnit.SECONDS);
        // lazy writer for the remembered last-server map
        scheduler.scheduleWithFixedDelay(this::flushLastServersIfNeeded,
                5, 5, TimeUnit.SECONDS);
        logger.info("FfWhitelistSync active: pulling {} every {}s",
                feedUri.getHost(), period);
    }

    @Subscribe
    public void onProxyShutdown(ProxyShutdownEvent event) {
        if (scheduler != null) {
            flushLastServersIfNeeded();
            scheduler.shutdownNow();
        }
    }

    // ------------------------------------------------------------------------
    // Presence reporting (who is live on the network, incl. server assoc.)
    // ------------------------------------------------------------------------
    private final java.util.Map<UUID, Long> joinTimes =
        new java.util.concurrent.ConcurrentHashMap<>();

    private volatile boolean presenceHealthy = true;

    @Subscribe
    public void onConnected(com.velocitypowered.api.event.player.ServerConnectedEvent event) {
        joinTimes.put(event.getPlayer().getUniqueId(), System.currentTimeMillis());
        lastServers.put(event.getPlayer().getUniqueId(),
                event.getServer().getServerInfo().getName());
        lastServersDirty.set(true);
        reportPresenceSoon();
    }

    @Subscribe
    public void onDisconnect(com.velocitypowered.api.event.connection.DisconnectEvent event) {
        joinTimes.remove(event.getPlayer().getUniqueId());
        reportPresenceSoon();
    }

    @Subscribe
    public void onServerSwitch(com.velocitypowered.api.event.player.ServerPreConnectEvent event) {
        // after the switch completes, the heartbeat picks the new server up;
        // report immediately for snappy UI updates
        event.getResult().getServer().ifPresent(rs ->
            scheduler.schedule(this::safeReportPresence, 1, TimeUnit.SECONDS));
    }

    private void reportPresenceSoon() {
        if (scheduler != null && !scheduler.isShutdown()) {
            scheduler.schedule(this::safeReportPresence, 500,
                    TimeUnit.MILLISECONDS);
        }
    }

    private void safeReportPresence() {
        try {
            reportPresence();
            presenceHealthy = true;
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            if (presenceHealthy) {
                logger.warn("presence report failed: {}", e.toString());
                presenceHealthy = false;
            }
        }
    }

    // ------------------------------------------------------------------------
    // Remember-last-server routing
    //
    // Every successful backend connection records the player's server in a
    // persisted map; on the NEXT initial join (choose-initial-server phase,
    // i.e. before the try-list decides) the remembered server is selected.
    // Explicit /server switches flow through untouched — only the entry
    // point honours the memory, so a player who leaves on creative rejoins
    // on creative instead of falling back to the try-order default.
    // ------------------------------------------------------------------------

    /** uuid (dashed) -> server name, mirrored to last-servers.json. */
    private final java.util.Map<UUID, String> lastServers =
            new java.util.concurrent.ConcurrentHashMap<>();

    private final AtomicBoolean lastServersDirty = new AtomicBoolean(false);

    private void loadLastServers() {
        try {
            if (!Files.exists(lastServersJson)) {
                return;
            }
            JsonObject root = JsonParser.parseReader(
                            Files.newBufferedReader(lastServersJson,
                                    StandardCharsets.UTF_8))
                    .getAsJsonObject();
            for (var entry : root.entrySet()) {
                try {
                    lastServers.put(UUID.fromString(entry.getKey()),
                            entry.getValue().getAsString());
                } catch (IllegalArgumentException badEntry) {
                    logger.warn("last-servers.json: skipping corrupt "
                            + "entry {}", entry.getKey());
                }
            }
            logger.info("loaded {} remembered last-server mappings",
                    lastServers.size());
        } catch (Exception e) {
            logger.warn("could not load last-servers.json: {}", e.toString());
        }
    }

    /**
     * Flushes the remembered map when marked dirty. Runs on the shared
     * scheduler thread (small file, low frequency — contention irrelevant);
     * dirty-clear-before-write guarantees a late mutation triggers another
     * flush instead of being swallowed.
     */
    private void flushLastServersIfNeeded() {
        if (!lastServersDirty.compareAndSet(true, false)) {
            return;
        }
        try {
            java.util.Map<String, String> ordered = new TreeMap<>();
            for (var e : lastServers.entrySet()) {
                ordered.put(e.getKey().toString(), e.getValue());
            }
            JsonObject root = new JsonObject();
            for (var e : ordered.entrySet()) {
                root.addProperty(e.getKey(), e.getValue());
            }
            Path tmp = lastServersJson.resolveSibling(
                    lastServersJson.getFileName() + ".tmp");
            Files.writeString(tmp, root.toString() + "\n",
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.TRUNCATE_EXISTING);
            try {
                Files.move(tmp, lastServersJson,
                        StandardCopyOption.REPLACE_EXISTING,
                        StandardCopyOption.ATOMIC_MOVE);
            } catch (AtomicMoveNotSupportedException e) {
                Files.move(tmp, lastServersJson,
                        StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (Exception e) {
            lastServersDirty.set(true); // retry on the next tick
            logger.warn("persisting last-servers.json failed: {}",
                    e.toString());
        }
    }

    @Subscribe
    public void onChooseInitialServer(PlayerChooseInitialServerEvent event) {
        String remembered = lastServers.get(event.getPlayer().getUniqueId());
        if (remembered == null) {
            return; // first-ever visit: default try-order applies
        }
        proxy.getServer(remembered).ifPresentOrElse(target -> {
            event.setInitialServer(target);
            logger.info("routing {} to remembered server '{}'",
                    event.getPlayer().getUsername(), remembered);
        }, () -> logger.warn(
                "remembered server '{}' no longer registered for {}; "
                        + "using default try-order", remembered,
                event.getPlayer().getUsername()));
    }

    private void reportPresence() throws IOException, InterruptedException {
        com.google.gson.JsonObject root = new com.google.gson.JsonObject();
        com.google.gson.JsonArray arr = new com.google.gson.JsonArray();
        for (Player p : proxy.getAllPlayers()) {
            com.google.gson.JsonObject o = new com.google.gson.JsonObject();
            o.addProperty("name", p.getUsername());
            o.addProperty("uuid", p.getUniqueId().toString().replace("-", ""));
            String server = p.getCurrentServer()
                    .map(sc -> sc.getServerInfo().getName())
                    .orElse("");
            o.addProperty("server", server);
            Long since = joinTimes.get(p.getUniqueId());
            o.addProperty("since", since == null ? 0 : since / 1000.0);
            arr.add(o);
        }
        root.add("players", arr);

        HttpRequest req = HttpRequest.newBuilder(
                    feedUri.resolve("/api/sync/players"))
                .timeout(Duration.ofSeconds(8))
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + authToken)
                .POST(HttpRequest.BodyPublishers.ofString(
                        root.toString(), StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> resp =
                http.send(req, HttpResponse.BodyHandlers.ofString());
        if (resp.statusCode() != 200) {
            throw new IllegalStateException(
                    "presence endpoint answered " + resp.statusCode());
        }
    }

    // ------------------------------------------------------------------------
    // Enforcement
    // ------------------------------------------------------------------------
    @Subscribe
    public void onLogin(LoginEvent event) {
        UUID id = event.getPlayer().getUniqueId();
        // allowedUuids stores 32-char hex (no dashes) — normalize before
        // comparing, Velocity's toString() yields the dashed form.
        String normalized = id.toString().replace("-", "");
        logger.info("login-check: player={} uuid-normalized={} allowed={}",
                event.getPlayer().getUsername(), normalized, allowedUuids);
        if (!allowedUuids.contains(normalized)) {
            event.setResult(com.velocitypowered.api.event.ResultedEvent
                    .ComponentResult.denied(Component.text(
                            "Du bist nicht freigeschaltet. Registriere dich "
                                    + "auf mc.max-kuehn.com "
                                    + "(Einladung erforderlich).",
                            NamedTextColor.RED)));
        }
    }

    // ------------------------------------------------------------------------
    // Refresh cycle
    // ------------------------------------------------------------------------
    private void safeRefresh() {
        try {
            refreshOnce();
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            logger.warn("whitelist refresh failed: {}", e.toString());
        }
    }

    private void refreshOnce() throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(feedUri)
                .timeout(Duration.ofSeconds(10))
                .header("Accept", "application/json")
                .header("Authorization", "Bearer " + authToken)
                .GET()
                .build();
        HttpResponse<String> resp =
                http.send(req, HttpResponse.BodyHandlers.ofString());

        if (resp.statusCode() != 200) {
            throw new IllegalStateException(
                    "feed answered " + resp.statusCode());
        }

        JsonObject root = JsonParser.parseString(resp.body())
                .getAsJsonObject();
        long revision = root.has("revision")
                ? root.get("revision").getAsLong() : -1;
        JsonArray entries = root.getAsJsonArray("entries");

        Set<String> fresh = new HashSet<>();
        StringBuilder sigSrc = new StringBuilder();
        for (var el : entries) {
            JsonObject obj = el.getAsJsonObject();
            String uuid = obj.get("uuid").getAsString().trim().toLowerCase();
            String name = obj.has("name") && !obj.get("name").isJsonNull()
                    ? obj.get("name").getAsString() : "?";
            if (!isValidHexUuid(uuid)) {
                logger.warn("feed contained malformed uuid, skipping: {}",
                        uuid);
                continue;
            }
            fresh.add(uuid);
            sigSrc.append(uuid).append('=').append(name.toLowerCase()).append(';');
        }

        String signature = Integer.toHexString(sigSrc.toString().hashCode());
        boolean contentChanged = !signature.equals(lastMirrorSignature);
        boolean revisionAdvanced = revision > lastAppliedRevision;

        if (!contentChanged && !revisionAdvanced) {
            return; // nothing to do
        }

        allowedUuids = Set.copyOf(fresh);
        lastAppliedRevision = Math.max(lastAppliedRevision, revision);
        lastMirrorSignature = signature;
        writeVanillaMirror(entries);
        logger.info("whitelist updated: {} players (revision {})",
                fresh.size(), revision);
    }

    private void writeVanillaMirror(JsonArray entries) throws IOException {
        StringBuilder sb = new StringBuilder("[\n");
        boolean first = true;
        for (var el : entries) {
            JsonObject obj = el.getAsJsonObject();
            if (!first) sb.append(",\n");
            String uuid = obj.get("uuid").getAsString().trim().toLowerCase();
            String dashed = dashUuid(uuid);
            String name = escape(obj.get("name").getAsString());
            sb.append("  {\n")
              .append("    \"uuid\": \"").append(dashed).append("\",\n")
              .append("    \"name\": \"").append(name).append("\"\n")
              .append("  }");
            first = false;
        }
        sb.append("\n]\n");

        Path tmp = whitelistJson.resolveSibling(
                whitelistJson.getFileName() + ".tmp");
        Files.writeString(tmp, sb.toString(), StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
        try {
            Files.move(tmp, whitelistJson,
                    StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException e) {
            Files.move(tmp, whitelistJson,
                    StandardCopyOption.REPLACE_EXISTING);
        }
    }

    // ------------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------------
    private void loadConfiguration() throws IOException {
        Files.createDirectories(dataDir);
        Path configFile = dataDir.resolve("config.properties");
        if (!Files.exists(configFile)) {
            String example = """
                    # FriendlyFire whitelist feed
                    feed-url=https://mc.max-kuehn.com/api/sync/whitelist
                    token=PASTE_BEARER_TOKEN_HERE
                    interval-seconds=60
                    """;
            Files.writeString(configFile, example, StandardCharsets.UTF_8);
            throw new IOException("created starter config at " + configFile
                    + "; fill in the token and restart");
        }
        Properties props = new Properties();
        try (var in = Files.newInputStream(configFile)) {
            props.load(in);
        }
        String urlProp = props.getProperty("feed-url", "");
        String token = props.getProperty("token", "");
        long secs = Long.parseLong(props.getProperty("interval-seconds", "60"));
        if (urlProp.isBlank() || token.isBlank()
                || "PASTE_BEARER_TOKEN_HERE".equals(token)) {
            throw new IOException("feed-url/token not configured");
        }
        feedUri = URI.create(urlProp);
        authToken = token;
        interval = Duration.ofSeconds(secs);
        lastServersJson = dataDir.resolve("last-servers.json");
        whitelistJson = dataDir.getParent()
                .resolve("global-whitelist")
                .resolve("whitelist.json");
        if (!Files.exists(whitelistJson.getParent())) {
            Files.createDirectories(whitelistJson.getParent());
        }
    }

    private void preloadLocalCache() {
        Set<String> cached = new HashSet<>();
        try {
            if (Files.exists(whitelistJson)) {
                JsonArray arr = JsonParser.parseReader(
                                Files.newBufferedReader(whitelistJson,
                                        StandardCharsets.UTF_8))
                        .getAsJsonArray();
                for (var el : arr) {
                    String dashed = el.getAsJsonObject()
                            .get("uuid").getAsString();
                    cached.add(dashed.replace("-", ""));
                }
            }
        } catch (Exception e) {
            logger.warn("could not preload whitelist cache: {}", e.toString());
        }
        allowedUuids = Set.copyOf(cached);
        logger.info("preload: {} uuids from local cache", cached.size());
    }

    private static boolean isValidHexUuid(String s) {
        return s.matches("[0-9a-f]{32}");
    }

    private static String dashUuid(String hex) {
        return hex.substring(0, 8) + "-" + hex.substring(8, 12) + "-"
                + hex.substring(12, 16) + "-" + hex.substring(16, 20) + "-"
                + hex.substring(20);
    }

    private static String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
