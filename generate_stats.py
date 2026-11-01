#!/usr/bin/env python3
"""Liest die Spielerstatistiken eines Minecraft-Servers und schreibt stats.json.

Zwei Wege, an die Daten zu kommen:

  1) Lokal (Server läuft auf deinem eigenen Rechner):
       python generate_stats.py --local /pfad/zum/server

  2) Per SFTP (Hoster mit SFTP-Zugang). Zugangsdaten als Umgebungsvariablen:
       SFTP_HOST, SFTP_PORT (Standard 22), SFTP_USER, SFTP_PASS, SFTP_ROOT (Standard /)
       pip install paramiko
       python generate_stats.py --sftp

Optional: --world NAME (Standard: world), --out PFAD (Standard: stats.json neben dem Skript)

Hinweis: Minecraft zählt "Blöcke platziert" nicht direkt. Das Skript nimmt dafür alle
benutzten Gegenstände und lässt Werkzeuge, Waffen, Rüstung, Essen und ähnliches weg.
Der Wert ist deshalb eine Näherung.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DISTANCE_KEYS = (
    "minecraft:walk_one_cm", "minecraft:sprint_one_cm", "minecraft:crouch_one_cm",
    "minecraft:swim_one_cm", "minecraft:aviate_one_cm", "minecraft:boat_one_cm",
    "minecraft:horse_one_cm", "minecraft:minecart_one_cm",
)

# Gegenstände, die beim Benutzen keinen Block setzen
NOT_BLOCK_SUFFIXES = (
    "_sword", "_axe", "_pickaxe", "_shovel", "_hoe", "_helmet", "_chestplate", "_leggings",
    "_boots", "_bucket", "_spawn_egg", "_boat", "_raft", "_minecart", "_horse_armor", "_dye",
    "_stew", "_soup", "_apple", "_pattern", "_smithing_template", "_nautilus_armor",
)
NOT_BLOCK_PREFIXES = ("cooked_", "raw_", "music_disc_", "potion", "splash_potion", "lingering_potion")
NOT_BLOCK_EXACT = {
    "bow", "crossbow", "trident", "shield", "shears", "flint_and_steel", "fishing_rod",
    "carrot_on_a_stick", "warped_fungus_on_a_stick", "ender_pearl", "ender_eye", "snowball",
    "egg", "firework_rocket", "bread", "apple", "golden_apple", "enchanted_golden_apple",
    "cookie", "melon_slice", "pumpkin_pie", "cake_slice", "milk_bucket", "bone_meal",
    "spyglass", "compass", "clock", "map", "filled_map", "lead", "name_tag", "saddle",
    "elytra", "totem_of_undying", "wind_charge", "fire_charge", "experience_bottle",
    "honey_bottle", "rotten_flesh", "spider_eye", "dried_kelp", "sweet_berries",
    "glow_berries", "chorus_fruit", "beetroot", "mushroom_stew", "rabbit_stew",
    "brush", "mace", "goat_horn", "writable_book", "written_book", "book", "paper",
}


def looks_like_block(item_id):
    name = item_id.split(":", 1)[-1]
    if name in NOT_BLOCK_EXACT:
        return False
    if name.endswith(NOT_BLOCK_SUFFIXES) or name.startswith(NOT_BLOCK_PREFIXES):
        return False
    return True


class LocalSource:
    def __init__(self, root):
        self.root = root

    def list_dir(self, rel):
        path = os.path.join(self.root, rel)
        return os.listdir(path) if os.path.isdir(path) else []

    def read_json(self, rel):
        path = os.path.join(self.root, rel)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)


class SftpSource:
    def __init__(self, host, port, user, password, root):
        try:
            import paramiko
        except ImportError:
            sys.exit("Für SFTP bitte zuerst installieren: pip install paramiko")
        transport = paramiko.Transport((host, port))
        transport.connect(username=user, password=password)
        self.sftp = paramiko.SFTPClient.from_transport(transport)
        self.root = root.rstrip("/")

    def list_dir(self, rel):
        try:
            return self.sftp.listdir(f"{self.root}/{rel}")
        except IOError:
            return []

    def read_json(self, rel):
        try:
            with self.sftp.open(f"{self.root}/{rel}") as f:
                return json.load(f)
        except IOError:
            return None


def load_names(source):
    cache = source.read_json("usercache.json") or []
    return {entry["uuid"].lower(): entry["name"] for entry in cache if "uuid" in entry and "name" in entry}


def summarize(uuid, data, names):
    stats = data.get("stats", {})
    custom = stats.get("minecraft:custom", {})
    # Neuere Versionen: play_time, ältere: play_one_minute (beides in Ticks)
    ticks = custom.get("minecraft:play_time", custom.get("minecraft:play_one_minute", 0))
    used = stats.get("minecraft:used", {})
    return {
        "name": names.get(uuid.lower(), uuid[:8]),
        "uuid": uuid,
        "playtimeHours": round(ticks / 20 / 3600, 1),
        "deaths": custom.get("minecraft:deaths", 0),
        "mobKills": custom.get("minecraft:mob_kills", 0),
        "playerKills": custom.get("minecraft:player_kills", 0),
        "blocksMined": sum(stats.get("minecraft:mined", {}).values()),
        "blocksPlaced": sum(n for item, n in used.items() if looks_like_block(item)),
        "distanceKm": round(sum(custom.get(k, 0) for k in DISTANCE_KEYS) / 100000, 1),
        "jumps": custom.get("minecraft:jump", 0),
        "itemsCrafted": sum(stats.get("minecraft:crafted", {}).values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", metavar="SERVERORDNER", help="Pfad zum Server-Hauptordner")
    parser.add_argument("--sftp", action="store_true", help="Daten per SFTP holen")
    parser.add_argument("--world", default="world", help="Name des Welt-Ordners (Standard: world)")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json"))
    args = parser.parse_args()

    if args.local:
        source = LocalSource(args.local)
    elif args.sftp:
        try:
            source = SftpSource(
                os.environ["SFTP_HOST"], int(os.environ.get("SFTP_PORT", "22")),
                os.environ["SFTP_USER"], os.environ["SFTP_PASS"], os.environ.get("SFTP_ROOT", "/"),
            )
        except KeyError as missing:
            sys.exit(f"Umgebungsvariable fehlt: {missing}")
    else:
        parser.error("Bitte --local oder --sftp angeben.")

    names = load_names(source)
    players = []
    for fname in source.list_dir(f"{args.world}/stats"):
        if not fname.endswith(".json"):
            continue
        data = source.read_json(f"{args.world}/stats/{fname}")
        if not data:
            continue
        player = summarize(fname[:-5], data, names)
        if player["playtimeHours"] > 0:
            players.append(player)

    players.sort(key=lambda p: p["playtimeHours"], reverse=True)
    result = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "players": players}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"{len(players)} Spieler nach {args.out} geschrieben.")


if __name__ == "__main__":
    main()
