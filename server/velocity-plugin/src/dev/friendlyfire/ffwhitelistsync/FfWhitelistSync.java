package dev.friendlyfire.ffwhitelistsync;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.velocitypowered.api.event.Subscribe;
import com.velocitypowered.api.event.connection.LoginEvent;
import com.velocitypowered.api.event.proxy.ProxyInitializeEvent;
import com.velocitypowered.api.event.proxy.ProxyShutdownEvent;
import com.velocitypowered.api.plugin.Plugin;
import com.velocitypowered.api.plugin.annotation.DataDirectory;
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
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

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
        version = "1.0.0",
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
        logger.info("FfWhitelistSync active: pulling {} every {}s",
                feedUri.getHost(), period);
    }

    @Subscribe
    public void onProxyShutdown(ProxyShutdownEvent event) {
        if (scheduler != null) {
            scheduler.shutdownNow();
        }
    }

    // ------------------------------------------------------------------------
    // Enforcement
    // ------------------------------------------------------------------------
    @Subscribe
    public void onLogin(LoginEvent event) {
        UUID id = event.getPlayer().getUniqueId();
        if (!allowedUuids.contains(id.toString())) {
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
