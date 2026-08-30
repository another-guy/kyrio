import json
import pathlib
import shutil
import tempfile
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import config


class LayerTree(unittest.TestCase):
    """Builds a synthetic directory tree of config layers (I8)."""

    def setUp(self):
        # .resolve(): mkdtemp can return an 8.3 short path, while the
        # resolver normalizes. Compare like with like.
        self.root = pathlib.Path(
            tempfile.mkdtemp(prefix="kyrio-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.machine = self.root / "machine" / "config.json"

    def write(self, relative, data, schema=config.SCHEMA_VERSION):
        """Write a layer at ``<relative>/.kyrio/config.json``."""
        directory = self.root / relative if relative else self.root
        path = directory / config.CONFIG_DIRNAME / config.CONFIG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        body = dict(data)
        if schema is not None:
            body.setdefault("schema", schema)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def write_machine(self, data, schema=config.SCHEMA_VERSION):
        self.machine.parent.mkdir(parents=True, exist_ok=True)
        body = dict(data)
        if schema is not None:
            body.setdefault("schema", schema)
        self.machine.write_text(json.dumps(body), encoding="utf-8")
        return self.machine

    def mkdirs(self, relative):
        directory = self.root / relative
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def resolve(self, start, machine=True):
        return config.resolve(
            start=start,
            machine_path=self.machine if machine else self.root / "absent.json")


class TestDiscovery(LayerTree):
    def test_machine_layer_alone(self):
        self.write_machine({"shell": "a"})
        cwd = self.mkdirs("code/product/repo")
        layers = config.discover(start=cwd, machine_path=self.machine)
        self.assertEqual([l.path for l in layers], [self.machine])

    def test_no_machine_layer_is_not_an_error(self):
        cwd = self.mkdirs("code/product/repo")
        layers = config.discover(start=cwd, machine_path=self.root / "absent.json")
        self.assertEqual(layers, [])

    def test_layers_are_ordered_base_first(self):
        machine = self.write_machine({"shell": "a"})
        workspace = self.write("", {"shell": "b"})
        product = self.write("code/product", {"shell": "c"})
        repo = self.write("code/product/repo", {"shell": "d"})
        cwd = self.mkdirs("code/product/repo/src/api")
        layers = config.discover(start=cwd, machine_path=self.machine)
        self.assertEqual([l.path for l in layers],
                         [machine, workspace, product, repo])

    def test_directories_without_a_layer_contribute_nothing(self):
        self.write_machine({"shell": "a"})
        repo = self.write("code/product/repo", {"shell": "d"})
        cwd = self.mkdirs("code/product/repo/src/api")
        layers = config.discover(start=cwd, machine_path=self.machine)
        self.assertEqual([l.path for l in layers], [self.machine, repo])

    def test_root_true_stops_the_upward_walk(self):
        self.write_machine({"shell": "a"})
        self.write("", {"shell": "b"})  # outside the root boundary
        product = self.write("code/product", {"shell": "c", "root": True})
        repo = self.write("code/product/repo", {"shell": "d"})
        cwd = self.mkdirs("code/product/repo/src")
        layers = config.discover(start=cwd, machine_path=self.machine)
        self.assertEqual([l.path for l in layers], [self.machine, product, repo])

    def test_root_layer_still_contributes(self):
        product = self.write("code/product", {"shell": "c", "root": True})
        cwd = self.mkdirs("code/product/repo")
        layers = config.discover(start=cwd, machine_path=self.root / "absent.json")
        self.assertEqual([l.path for l in layers], [product])
        self.assertTrue(layers[0].is_root)

    def test_start_defaults_to_cwd_without_raising(self):
        self.assertIsInstance(
            config.discover(machine_path=self.root / "absent.json"), list)


class TestLayerValidation(LayerTree):
    def test_malformed_json_names_the_file_and_the_position(self):
        path = self.root / config.CONFIG_DIRNAME / config.CONFIG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schema": 1,,}', encoding="utf-8")
        with self.assertRaises(config.ConfigError) as ctx:
            config.read_layer(path)
        self.assertIn(str(path), str(ctx.exception))
        self.assertIn("line", str(ctx.exception))

    def test_missing_schema_is_rejected(self):
        path = self.write("repo", {"shell": "a"}, schema=None)
        with self.assertRaises(config.ConfigError) as ctx:
            config.read_layer(path)
        self.assertIn("schema", str(ctx.exception))

    def test_future_schema_is_rejected_rather_than_half_read(self):
        path = self.write("repo", {"shell": "a"}, schema=2)
        with self.assertRaises(config.ConfigError):
            config.read_layer(path)

    def test_non_object_is_rejected(self):
        path = self.root / config.CONFIG_DIRNAME / config.CONFIG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            config.read_layer(path)

    def test_meta_keys_are_not_configuration_values(self):
        path = self.write("repo", {"shell": "a", "root": True})
        layer = config.read_layer(path)
        self.assertEqual(layer.values(), {"shell": "a"})


class TestStrategyLookup(unittest.TestCase):
    def test_unlisted_key_defaults_to_nearest_wins(self):
        self.assertEqual(config.strategy_for("shell"), config.NEAREST_WINS)

    def test_strategy_applies_beneath_its_path(self):
        self.assertEqual(config.strategy_for("capabilities"), config.DEEP_MERGE)
        self.assertEqual(config.strategy_for("capabilities.scm"),
                         config.DEEP_MERGE)
        self.assertEqual(config.strategy_for("capabilities.scm.transport"),
                         config.DEEP_MERGE)

    def test_prefix_match_is_on_path_segments_not_characters(self):
        self.assertEqual(config.strategy_for("capabilities_extra"),
                         config.NEAREST_WINS)

    def test_longest_matching_prefix_wins(self):
        original = dict(config.STRATEGIES)
        config.STRATEGIES["capabilities.scm.roots"] = config.UNION_APPEND
        try:
            self.assertEqual(config.strategy_for("capabilities.scm.roots"),
                             config.UNION_APPEND)
            self.assertEqual(config.strategy_for("capabilities.scm.transport"),
                             config.DEEP_MERGE)
        finally:
            config.STRATEGIES.clear()
            config.STRATEGIES.update(original)


class TestMergeStrategies(LayerTree):
    def test_nearest_wins_replaces(self):
        self.write_machine({"shell": "outer"})
        self.write("repo", {"shell": "inner"})
        resolved = self.resolve(self.mkdirs("repo/src"))
        self.assertEqual(resolved.get("shell"), "inner")

    def test_deep_merge_keeps_untouched_sibling_keys(self):
        self.write_machine({"capabilities": {
            "repo": {"transport": "local"},
            "scm": {"transport": "cli", "provider": "p"},
        }})
        self.write("repo", {"capabilities": {"scm": {"transport": "server"}}})
        resolved = self.resolve(self.mkdirs("repo/src"))
        self.assertEqual(resolved.get("capabilities.repo.transport"), "local")
        self.assertEqual(resolved.get("capabilities.scm.transport"), "server")
        self.assertEqual(resolved.get("capabilities.scm.provider"), "p",
                         "deep-merge must not drop keys the nearer layer omits")

    def test_deep_merge_introduces_new_keys(self):
        self.write_machine({"conventions": {"test": "t"}})
        self.write("repo", {"conventions": {"build": "b"}})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.get("conventions"), {"test": "t", "build": "b"})

    def test_union_append_accumulates_nearest_last(self):
        self.write_machine({"ignore": ["a", "b"]})
        self.write("repo", {"ignore": ["c"]})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.get("ignore"), ["a", "b", "c"])

    def test_union_append_deduplicates_preserving_first_appearance(self):
        self.write_machine({"ignore": ["a", "b"]})
        self.write("repo", {"ignore": ["b", "a", "c"]})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.get("ignore"), ["a", "b", "c"])

    def test_union_append_rejects_a_non_list(self):
        self.write_machine({"ignore": "a"})
        with self.assertRaises(config.ConfigError):
            self.resolve(self.mkdirs("repo"))

    def test_deep_merge_key_holding_a_scalar_falls_back_to_replacement(self):
        self.write_machine({"capabilities": {"scm": {"transport": "cli"}}})
        self.write("repo", {"capabilities": {"scm": "disabled"}})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.get("capabilities.scm"), "disabled")


class TestMonotonicTighten(unittest.TestCase):
    """Defined and reserved for D1; tested so it cannot rot before use."""

    def test_narrowing_a_list_is_allowed(self):
        self.assertEqual(config._tighten(["a", "b", "c"], ["a", "b"], "k"),
                         ["a", "b"])

    def test_widening_a_list_is_rejected_and_names_the_offenders(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config._tighten(["a"], ["a", "b"], "k")
        self.assertIn("'b'", str(ctx.exception))

    def test_first_layer_sets_the_ceiling(self):
        self.assertEqual(config._tighten(None, ["a"], "k"), ["a"])

    def test_a_boolean_may_be_enabled_but_not_disabled(self):
        self.assertIs(config._tighten(False, True, "k"), True)
        with self.assertRaises(config.ConfigError):
            config._tighten(True, False, "k")

    def test_undefined_for_other_types(self):
        with self.assertRaises(config.ConfigError):
            config._tighten(1, 2, "k")

    def test_reachable_through_the_strategy_table(self):
        original = dict(config.STRATEGIES)
        config.STRATEGIES["allow"] = config.MONOTONIC_TIGHTEN
        try:
            acc, prov = {}, {}
            config._merge_into(acc, {"allow": ["a", "b"]}, "", prov, "outer")
            with self.assertRaises(config.ConfigError):
                config._merge_into(acc, {"allow": ["c"]}, "", prov, "inner")
        finally:
            config.STRATEGIES.clear()
            config.STRATEGIES.update(original)


class TestProvenance(LayerTree):
    def test_a_replaced_value_records_every_layer_that_set_it(self):
        machine = self.write_machine({"shell": "outer"})
        repo = self.write("repo", {"shell": "inner"})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.sources("shell"), [str(machine), str(repo)])

    def test_provenance_is_recorded_per_leaf_for_deep_merge(self):
        machine = self.write_machine({"capabilities": {
            "repo": {"transport": "local"},
            "scm": {"transport": "cli"},
        }})
        repo = self.write("repo", {"capabilities": {"scm": {"transport": "server"}}})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.sources("capabilities.repo.transport"),
                         [str(machine)])
        self.assertEqual(resolved.sources("capabilities.scm.transport"),
                         [str(machine), str(repo)])

    def test_union_records_all_contributors(self):
        machine = self.write_machine({"ignore": ["a"]})
        repo = self.write("repo", {"ignore": ["b"]})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.sources("ignore"), [str(machine), str(repo)])

    def test_unknown_path_has_no_sources(self):
        self.write_machine({"shell": "a"})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.sources("nope"), [])


class TestResolvedAccess(LayerTree):
    def test_get_walks_a_dotted_path(self):
        self.write_machine({"catalog": {"svc": {"repo": "r"}}})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(resolved.get("catalog.svc.repo"), "r")

    def test_get_returns_the_default_for_a_missing_path(self):
        self.write_machine({"shell": "a"})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertIsNone(resolved.get("catalog.svc.repo"))
        self.assertEqual(resolved.get("catalog.svc.repo", "d"), "d")

    def test_get_does_not_descend_into_a_scalar(self):
        self.write_machine({"shell": "a"})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertIsNone(resolved.get("shell.flavor"))

    def test_layers_are_retained_for_reporting(self):
        self.write_machine({"shell": "a"})
        self.write("repo", {"shell": "b"})
        resolved = self.resolve(self.mkdirs("repo"))
        self.assertEqual(len(resolved.layers), 2)


if __name__ == "__main__":
    unittest.main()
