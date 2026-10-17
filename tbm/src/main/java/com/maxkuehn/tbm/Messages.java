package com.maxkuehn.tbm;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;

import java.util.Locale;
import java.util.Map;

/**
 * Player-facing message localization. Language is set in
 * {@code config/tbm.json} ({@code "lang": "de"} or {@code "en"});
 * defaults to German. Command names are English in both languages.
 */
final class Messages {

    private Messages() {
    }

    static String lang = "de";

    private static final Map<String, String> DE = Map.ofEntries(
            Map.entry("prefix", "[TBM]"),
            Map.entry("activate.success", "\u00a7aTunnelbohrmaschine aktiviert (\u00a76%d\u00a7a Bl\u00f6cke Front\u00a7a)%s."),
            Map.entry("activate.placer", ", Placer: %d Modul(e)"),
            Map.entry("activate.already-active", "Diese Maschine ist bereits aktiviert."),
            Map.entry("scan.sign-not-wall", "Das Schild muss ein Wandschild an einer Eisewand sein."),
            Map.entry("scan.bad-label", "Erste Schildzeile muss exakt \u00a76[TBM]\u00a7r sein."),
            Map.entry("scan.no-attach-iron", "Hinter dem Schild muss ein Eisenblock sitzen."),
            Map.entry("scan.no-furnace", "Unter dem Schildhalter muss ein Ofen eingebaut sein (Generator)."),
            Map.entry("scan.face-too-large", "Front gr\u00f6\u00dfer als %dx%d \u2013 nicht erlaubt."),
            Map.entry("scan.front-gap", "Loch in der Bohrfront bei \u00a76%s\u00a7r \u2013 die Front muss geschlossen sein."),
            Map.entry("scan.foreign-in-face", "Fremdblock in der Frontebene bei \u00a76%s\u00a7r (nur Pistons erlaubt)."),
            Map.entry("scan.piston-without-iron", "Piston bei \u00a76%s\u00a7r hat keinen Eisenblock direkt dahinter."),
            Map.entry("scan.wall-disconnected", "Eisenwand nicht verbunden \u2013 Wand muss zusammenh\u00e4ngen und den Ofen enthalten."),
            Map.entry("scan.orphan-barrel", "Barrel bei \u00a76%s\u00a7r ohne zugeordneten R\u00fcck-Piston (Paar: Barrel vor Piston)."),
            Map.entry("protect.denied", "Bestandteil einer aktiven TBM \u2013 erst \u00a7e/tbm deactivate\u00a7r."),
            Map.entry("deactivated", "\u00a7eTBM deaktiviert (%s)."),
            Map.entry("cmd.list.empty", "Keine TBMs registriert."),
            Map.entry("cmd.players-only", "Nur f\u00fcr Spieler."),
            Map.entry("cmd.activate.not-a-sign", "An dieser Position ist kein Schild."),
            Map.entry("cmd.list.header", "Registrierte TBMs (%d):"),
            Map.entry("cmd.list.row", "%s @ %s, Richtung \u00a76%s\u00a7r, Front %d Bl\u00f6cke%s"),
            Map.entry("cmd.list.active", "\u00a7aantrieb aktiv\u00a7r"),
            Map.entry("cmd.list.idle", "\u00a77ohne Brennstoff\u00a7r"),
            Map.entry("cmd.deactivate.none", "Du musst auf ein TBM-Schild oder in die Maschine schauen (max. 8 Bl\u00f6cke)."),
            Map.entry("cmd.deactivated", "\u00a7aTBM deaktiviert.")
    );

    private static final Map<String, String> EN = Map.ofEntries(
            Map.entry("prefix", "[TBM]"),
            Map.entry("activate.success", "\u00a7aTunnel boring machine activated (\u00a76%d\u00a7a face blocks\u00a7a)%s."),
            Map.entry("activate.placer", ", placer: %d module(s)"),
            Map.entry("activate.already-active", "This machine is already active."),
            Map.entry("scan.sign-not-wall", "The sign must be a wall sign mounted on an iron wall."),
            Map.entry("scan.bad-label", "First sign line must be exactly \u00a76[TBM]\u00a7r."),
            Map.entry("scan.no-attach-iron", "There must be an iron block behind the sign."),
            Map.entry("scan.no-furnace", "A furnace must sit below the sign mount (generator)."),
            Map.entry("scan.face-too-large", "Face larger than %dx%d \u2013 not allowed."),
            Map.entry("scan.front-gap", "Gap in the boring face at \u00a76%s\u00a7r \u2013 the face must be closed."),
            Map.entry("scan.foreign-in-face", "Foreign block in the face plane at \u00a76%s\u00a7r (only pistons allowed)."),
            Map.entry("scan.piston-without-iron", "Piston at \u00a76%s\u00a7r has no iron block directly behind it."),
            Map.entry("scan.wall-disconnected", "Iron wall disconnected \u2013 wall must be one piece and contain the furnace."),
            Map.entry("scan.orphan-barrel", "Barrel at \u00a76%s\u00a7r without a paired rear piston (pair: barrel in front of piston)."),
            Map.entry("protect.denied", "Part of an active TBM \u2013 run \u00a7e/tbm deactivate\u00a7r first."),
            Map.entry("deactivated", "\u00a7eTBM deactivated (%s)."),
            Map.entry("cmd.list.empty", "No TBMs registered."),
            Map.entry("cmd.players-only", "Players only."),
            Map.entry("cmd.activate.not-a-sign", "There is no sign at this position."),
            Map.entry("cmd.list.header", "Registered TBMs (%d):"),
            Map.entry("cmd.list.row", "%s @ %s, direction \u00a76%s\u00a7r, face %d blocks%s"),
            Map.entry("cmd.list.active", "\u00a7adriving\u00a7r"),
            Map.entry("cmd.list.idle", "\u00a77out of fuel\u00a7r"),
            Map.entry("cmd.deactivate.none", "Look at a TBM sign or into the machine (max. 8 blocks)."),
            Map.entry("cmd.deactivated", "\u00a7aTBM deactivated.")
    );

    /** Raw localized pattern with legacy \u00a7 codes, %-style args unresolved. */
    static String raw(String key, Object... args) {
        Map<String, String> map = "en".equalsIgnoreCase(lang) ? EN : DE;
        String tpl = map.getOrDefault(key, map.containsKey(key) ? map.get(key) : key);
        String s = args.length == 0 ? tpl : String.format(Locale.ROOT, tpl, args);
        return map.get("prefix") + " " + s;
    }

    /** Localized message as a Component (legacy \u00a7 codes honored). */
    static Component msg(String key, Object... args) {
        return Component.literal(raw(key, args));
    }
}
