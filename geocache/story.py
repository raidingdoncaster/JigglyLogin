"""Static story content for the rebuilt geocache quest."""

STORY = {
    "title": "Whispers of the Wild Court",
    "version": 1,
    "start_scene": "market_note",
    "scenes": {
        "market_note": {
            "title": "Market Whispers",
            "summary": "A folded note invites you to explore.",
            "body": [
                "The Corn Exchange hums with anticipation. Traders whisper about sigils, wild lights, and a court that watches over Doncaster’s players.",
                "A folded note slides across the table. Ink glimmers with starlight. “Follow the Wild Court’s call and restore the balance.”",
            ],
            "options": [
                {"label": "Head into the arcade", "next": "arcade_entrance"},
                {"label": "Pocket the note and leave", "action": "reset"},
            ],
        },
        "arcade_entrance": {
            "title": "Arcade Echoes",
            "summary": "Lanterns guide your steps beneath the arches.",
            "body": [
                "Lanterns flicker to life as you step under the arches. Faint sigils shimmer on the stone, guiding you deeper into the arcade.",
                "Three leads emerge: a compass stall, a crystal reader, and a winding staircase that climbs toward the skylight.",
            ],
            "options": [
                {"label": "Investigate the compass stall", "next": "compass_stall"},
                {"label": "Visit the crystal reader", "next": "crystal_reader"},
                {"label": "Climb the staircase", "next": "skylight_balcony"},
            ],
        },
        "compass_stall": {
            "title": "The Cracked Compass",
            "summary": "A puzzle waits to be solved.",
            "body": [
                "A brass compass sits on velvet, its glass fractured yet radiating a warm glow.",
                "The stall keeper nods. “Realign the shards and the Wild Court will share its bearing.”",
            ],
            "options": [
                {"label": "Piece together the compass", "next": "compass_aligned"},
                {"label": "Return to the arcade", "next": "arcade_entrance"},
            ],
        },
        "crystal_reader": {
            "title": "Crystal Visions",
            "summary": "Possible paths flicker within the lens.",
            "body": [
                "A crystal orb swirls with phosphorescent light. Shapes of Doncaster landmarks shimmer into view.",
                "The reader smiles knowingly. “The compass, the balcony, the market square—each step pulls you closer to the court.”",
            ],
            "options": [
                {"label": "Return to the arcade", "next": "arcade_entrance"},
            ],
        },
        "skylight_balcony": {
            "title": "Staircase to the Skylight",
            "summary": "The city stretches below.",
            "body": [
                "You climb the ornate staircase to a skylit balcony. Doncaster sprawls beyond, patterned with lights and possibility.",
                "Far below, the compass stall glows brighter. Perhaps the Wild Court is nudging you back to finish what you started.",
            ],
            "options": [
                {"label": "Descend to the compass stall", "next": "compass_stall"},
                {"label": "Return to the arcade", "next": "arcade_entrance"},
            ],
        },
        "compass_aligned": {
            "title": "Alignment Complete",
            "summary": "Threads of light reveal the crest.",
            "body": [
                "With the final shard in place, the compass spins to life. Lines of light arc across the arcade and converge on the Wild Court crest.",
                "Balance is restored—for now. The crest lingers, promising new chapters for Trainers who answer the call.",
            ],
            "options": [
                {"label": "Replay the quest", "action": "replay"},
                {"label": "Return to landing", "action": "reset"},
            ],
        },
    },
}
