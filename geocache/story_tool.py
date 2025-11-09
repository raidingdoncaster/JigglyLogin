from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from .services import REQUIRED_FLAGS_BY_ACT  # reuse progression map


def load_story(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Story file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def command_summary(story: Dict[str, Any]) -> None:
    assets_path = Path(__file__).resolve().parent.parent / "data" / "geocache_assets.json"
    try:
        assets = json.loads(assets_path.read_text())
    except Exception:
        assets = {"locations": [], "artifacts": []}

    location_index = {entry.get("scene_id") or entry.get("id"): entry for entry in assets.get("locations", [])}
    artifact_index = {entry.get("scene_id") or entry.get("slug"): entry for entry in assets.get("artifacts", [])}

    acts = story.get("acts", [])
    scenes: Dict[str, Any] = story.get("scenes", {})
    print("== Geocache Quest Summary ==")
    print(f"Title: {story.get('title', 'Unknown')}")
    print(f"Acts: {len(acts)} | Scenes: {len(scenes)}")
    print()

    for act in acts:
        act_id = act.get("id")
        title = act.get("title")
        scene_ids: List[str] = act.get("scenes") or []
        required_flags = REQUIRED_FLAGS_BY_ACT.get(int(act_id.replace("act", "")) if str(act_id).startswith("act") else act_id, set())

        print(f"- {act_id}: {title}")
        if required_flags:
            print(f"  Required flags: {', '.join(sorted(required_flags))}")
        print(f"  Scenes ({len(scene_ids)}): {', '.join(scene_ids)}")
        locs = []
        artifacts = []
        for sid in scene_ids:
            scene = scenes.get(sid) or {}
            mg = scene.get("minigame") or {}
            if mg.get("kind") == "location":
                asset = location_index.get(sid) or location_index.get(mg.get("location_id"))
                lat = asset.get("latitude") if asset else mg.get("latitude")
                lng = asset.get("longitude") if asset else mg.get("longitude")
                radius = asset.get("radius_m") if asset else mg.get("radius_m")
                locs.append(
                    f"{mg.get('location_id') or sid} ({lat}, {lng}, radius {radius}m)"
                )
            elif mg.get("kind") == "artifact_scan":
                asset = artifact_index.get(sid) or artifact_index.get(mg.get("artifact_slug"))
                code = asset.get("code") if asset else mg.get("code")
                nfc_uid = asset.get("nfc_uid") if asset else mg.get("nfc_uid")
                artifacts.append(
                    f"{mg.get('artifact_slug') or sid} (code {code}, nfc {nfc_uid or 'n/a'})"
                )
        if locs:
            print("  Location targets:")
            for loc in locs:
                print(f"    - {loc}")
        if artifacts:
            print("  Artifacts:")
            for art in artifacts:
                print(f"    - {art}")
        print(f"  Next act: {act.get('next_act') or 'end'}\n")

def command_scene(story: Dict[str, Any], scene_id: str) -> None:
    scenes: Dict[str, Any] = story.get("scenes", {})
    scene = scenes.get(scene_id)
    if not scene:
        print(f"Scene '{scene_id}' not found.")
        return

    print(json.dumps(scene, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect geocache story graph.")
    parser.add_argument("command", choices=["summary", "scene"], help="Command to run")
    parser.add_argument("argument", nargs="?", help="Optional argument (scene id for 'scene')")
    parser.add_argument(
        "--path",
        default=Path(__file__).resolve().parent.parent / "data" / "geocache_story.json",
        type=Path,
        help="Path to geocache story JSON",
    )
    args = parser.parse_args()

    story = load_story(args.path)

    if args.command == "summary":
        command_summary(story)
    elif args.command == "scene":
        if not args.argument:
            parser.error("scene command requires a scene ID argument")
        command_scene(story, args.argument)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
