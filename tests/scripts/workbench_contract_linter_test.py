"""Multi-layer static contract verification test for Workbench.

Proves that:
1. Command opcodes declared in C++ ABI and Python backend have matching UI metadata.
2. Board edges and labels in backend SSOT match frontend expectations.
3. Declarative telemetry schemas and observation rates serialize correctly.
4. Frontend JavaScript modules and primitive exports are well-formed and consistent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from novakit.services.workbench import (  # noqa: E402
    commands,
    observations,
    paths,
    session,
)

UI_JS = ROOT / "web" / "workbench" / "js"


class WorkbenchContractLinterTest(unittest.TestCase):
    """Verifies contracts across Firmware ABI, Python Service, and JS UI."""

    def test_command_meta_covers_all_runtime_opcodes(self):
        """Every opcode in commands.OPS must have UI label/action in COMMAND_META."""
        for op_name in commands.OPS:
            with self.subTest(op=op_name):
                self.assertIn(
                    op_name,
                    commands.COMMAND_META,
                    f"Opcode '{op_name}' missing from commands.COMMAND_META",
                )
                meta = commands.COMMAND_META[op_name]
                self.assertIn("label", meta)
                self.assertIn("action", meta)
                self.assertTrue(meta["label"], f"Opcode '{op_name}' has empty label")
                self.assertTrue(meta["action"], f"Opcode '{op_name}' has empty action")

    def test_edge_labels_cover_all_declared_edges(self):
        """Every Edge defined in paths.EDGES must have a non-empty label."""
        for edge in paths.EDGES:
            with self.subTest(edge_id=edge.id):
                self.assertTrue(edge.label, f"Edge '{edge.id}' is missing a label in paths.EDGES")
                self.assertIn(
                    edge.id,
                    paths.EDGE_LABELS,
                    f"Edge '{edge.id}' missing from paths.EDGE_LABELS dictionary",
                )

    def test_initial_topology_bundles_ui_metadata(self):
        """Topology envelope must contain ui_metadata bundle with commands and edges."""
        topo = session.initial_topology()
        self.assertIn("ui_metadata", topo, "ui_metadata missing from initial_topology")
        ui_meta = topo["ui_metadata"]
        self.assertIn("commands", ui_meta)
        self.assertIn("edges", ui_meta)
        self.assertEqual(ui_meta["commands"], commands.COMMAND_META)
        self.assertEqual(ui_meta["edges"], paths.EDGE_LABELS)

    def test_observation_rates_serializes_cleanly(self):
        """observation_rates() returns dictionary matching OBSERVATIONS topics."""
        rates = observations.observation_rates()
        for obs in observations.OBSERVATIONS:
            with self.subTest(topic=obs.topic):
                self.assertIn(obs.topic, rates)
                entry = rates[obs.topic]
                self.assertEqual(entry["rate"], obs.rate_hz)
                if obs.as_of:
                    self.assertEqual(entry["as_of"], obs.as_of)

    def test_shared_primitive_modules_exist_and_export_expected_symbols(self):
        """Ensure all newly extracted primitive modules exist and declare their exports."""
        table_mjs = (UI_JS / "primitives" / "table.mjs").read_text()
        self.assertIn("export class Cell", table_mjs)
        self.assertIn("export class Cursor", table_mjs)
        self.assertIn("export class BareCell", table_mjs)
        self.assertIn("export function table(", table_mjs)
        self.assertIn("export function generic(", table_mjs)

        stream_log_mjs = (UI_JS / "primitives" / "stream_log.mjs").read_text()
        self.assertIn("export class StreamLog", stream_log_mjs)

        ui_kit_mjs = (UI_JS / "primitives" / "ui_kit.mjs").read_text()
        self.assertIn("export const EVIDENCE_GRADES", ui_kit_mjs)
        self.assertIn("export function evidenceBadge(", ui_kit_mjs)
        self.assertIn("export function chipButton(", ui_kit_mjs)

        bitfield_mjs = (UI_JS / "primitives" / "bitfield.mjs").read_text()
        self.assertIn("export const BITFIELD_TEMPLATES", bitfield_mjs)
        self.assertIn("export class BitfieldPopover", bitfield_mjs)
        self.assertIn("export const globalBitfieldPopover", bitfield_mjs)

    def test_panels_reexports_table_primitives_for_compatibility(self):
        """panels.mjs must continue to export Cell, Cursor, table, generic for consumers."""
        panels_mjs = (UI_JS / "panels.mjs").read_text()
        self.assertRegex(
            panels_mjs,
            r"export\s*\{\s*BareCell,\s*Cell,\s*Cursor,\s*generic,\s*note,\s*plain,\s*section,\s*table\s*\};",
        )


if __name__ == "__main__":
    unittest.main()
