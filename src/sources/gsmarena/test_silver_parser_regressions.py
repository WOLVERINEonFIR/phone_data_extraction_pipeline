"""Regression tests for recurring GSMArena Silver parser extraction failures."""
import unittest
from bs4 import BeautifulSoup

from silver_parser import (
    _network_configurations,
    _parse_dimensions,
    _parse_model_numbers,
    _parse_weight_options,
    _parse_device_type,
    _spec_multiline,
    parse_gsmarena_html,
)


class ParserRegressionTests(unittest.TestCase):
    def test_model_identifiers_cover_google_and_apple_formats(self):
        self.assertEqual(
            _parse_model_numbers("GEHN3, G4QUR, GN4F5"),
            ["GEHN3", "G4QUR", "GN4F5"],
        )
        self.assertEqual(
            _parse_model_numbers("iPhone18,4, A3517, A3260"),
            ["iPhone18,4", "A3517", "A3260"],
        )
        self.assertEqual(
            _parse_model_numbers("A2886, A2632, A2885, iphone14,8"),
            ["A2886", "A2632", "A2885", "iphone14,8"],
        )
        self.assertEqual(
            _parse_model_numbers("SM-F966B, SM-F966B/DS"),
            ["SM-F966B", "SM-F966B/DS"],
        )

    def test_foldable_thickness_range_is_not_collapsed(self):
        parsed = _parse_dimensions(
            "Unfolded: 159.2 x 214.1 x 3.9-4.2 mm "
            "Folded: 159.2 x 75 x 12.9 mm"
        )
        self.assertEqual(parsed["unfolded"]["length_mm"], 159.2)
        self.assertEqual(parsed["unfolded"]["width_mm"], 214.1)
        self.assertIsNone(parsed["unfolded"]["thickness_mm"])
        self.assertEqual(parsed["unfolded"]["thickness_range_mm"], [3.9, 4.2])
        self.assertEqual(parsed["folded"]["thickness_mm"], 12.9)

    def test_optional_band_rows_retain_google_model_qualifiers(self):
        html = """
        <table id="specs-list">
          <tr class="tr-toggle">
            <td class="ttl">4G bands</td>
            <td class="nfo" data-spec="net4g">1, 2, 3 - GEHN3</td>
          </tr>
          <tr class="tr-toggle" data-spec-optional="">
            <td class="ttl"></td>
            <td class="nfo">1, 2, 4 - G4QUR</td>
          </tr>
          <tr class="tr-toggle">
            <td class="ttl">5G bands</td>
            <td class="nfo" data-spec="net5g">1, 2, 3 SA/NSA/Sub6 - GEHN3</td>
          </tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        configs = _network_configurations(soup, "net4g")
        self.assertEqual([row["models"] for row in configs], [["GEHN3"], ["G4QUR"]])

    def test_status_nfc_and_wireless_wattage_are_preserved(self):
        html = """
        <h1 class="specs-phone-name-title">Apple iPhone Air</h1>
        <div data-spec="modelname">Apple iPhone Air</div>
        <div data-spec="status">Available. Released 2025, September 19</div>
        <div data-spec="released-hl">Released 2025, September 19</div>
        <div data-spec="models">iPhone18,4, A3517</div>
        <div data-spec="battype-hl">PD2.0 20W</div>
        <div data-spec="nfc">Yes (market/region dependent)</div>
        <table id="specs-list">
          <tr><td class="ttl">Charging</td>
              <td class="nfo">Wired, PD2.0, 20 wireless MagSafe/Qi2 (15W - China),
              4.5W reverse wired</td></tr>
        </table>
        """
        result = parse_gsmarena_html(html)
        self.assertEqual(result["physical"]["model_numbers"], ["iPhone18,4", "A3517"])
        self.assertEqual(result["battery"]["charging"]["wireless_watt"], 20)
        self.assertEqual(result["battery"]["charging"]["reverse_wired_watt"], 4.5)
        self.assertIn("15W - China", result["battery"]["charging"]["raw"])
        self.assertTrue(result["connectivity"]["has_nfc"])
        self.assertEqual(result["connectivity"]["nfc_raw"], "Yes (market/region dependent)")

    def test_camera_modules_use_plain_newline_separator(self):
        soup = BeautifulSoup(
            '<div data-spec="cam1modules">50 MP wide<br>12 MP ultrawide</div>',
            "html.parser",
        )
        result = _spec_multiline(soup, "cam1modules")
        self.assertEqual(result, "50 MP wide\n12 MP ultrawide")
        self.assertNotIn("\\\\\n", result)

    def test_battery_capacity_alternatives_and_commas_are_preserved(self):
        html = (
            '<div data-spec="modelname">Example Phone</div>'
            '<div data-spec="batsize">5,000 mAh or 5,500 mAh</div>'
        )
        result = parse_gsmarena_html(html)
        self.assertEqual(result["battery"]["capacity_mah"], 5000)
        self.assertEqual(result["battery"]["capacity_options_mah"], [5000, 5500])



    def test_battery_capacity_options_are_collected_from_battery_detail(self):
        cases = [
            (
                "Infinix GT 50 Pro",
                "6500",
                "6150 mAh or 6500 mAh",
                6500,
                [6500, 6150],
            ),
            (
                "Nothing Phone (4a)",
                "5080",
                "5080 mAh - International 5400 mAh - India",
                5080,
                [5080, 5400],
            ),
            (
                "Oppo A6 Pro (India)",
                "7000",
                "Si/C Li-Ion 7000 mAh",
                7000,
                [7000],
            ),
            (
                "Oppo A6 Pro 4G",
                "7000",
                "Market-dependent versions: Si/C Li-Ion "
                "7000 mAh - version 1 6500 mAh - version 2",
                7000,
                [7000, 6500],
            ),
        ]

        for name, spotlight, detail, expected_primary, expected_options in cases:
            html = f"""
            <div data-spec="batsize-hl">{spotlight}</div>
            <div data-spec="batdescription1">{detail}</div>
            """

            result = parse_gsmarena_html(html)

            assert result["battery"]["capacity_mah"] == expected_primary, name
            assert result["battery"]["capacity_options_mah"] == expected_options, name

        
    def test_battery_capacity_is_collected_from_repeated_spec_fields(self):
        html = """
        <div data-spec="modelname">Example Phone</div>
        <div data-spec="batsize">6500 mAh</div>
        <div data-spec="batsize">6150 mAh</div>
        """
        result = parse_gsmarena_html(html)
        self.assertEqual(result["battery"]["capacity_mah"], 6500)
        self.assertEqual(
            result["battery"]["capacity_options_mah"],
            [6500, 6150],
        )


    def test_bluetooth_alternatives_do_not_claim_one_universal_version(self):
        html = (
            '<div data-spec="modelname">Example Phone</div>'
            '<div data-spec="bluetooth">5.4/6.0, A2DP, LE</div>'
        )
        result = parse_gsmarena_html(html)
        self.assertIsNone(result["connectivity"]["bluetooth"]["version"])
        self.assertEqual(result["connectivity"]["bluetooth"]["versions"], ["5.4", "6.0"])
        self.assertIn("5.4/6.0", result["connectivity"]["bluetooth"]["raw"])

    def test_thickness_alternatives_are_nested_and_not_collapsed(self):
        parsed = _parse_dimensions("163.8 x 76.9 x 8.3 mm or 8.6 mm")
        self.assertIsNone(parsed["thickness_mm"])
        self.assertEqual(parsed["thickness_options_mm"], [8.3, 8.6])
        foldable = _parse_dimensions(
            "Unfolded: 159.2 x 214.1 x 3.9-4.2 mm Folded: 159.2 x 75 x 12.9 mm"
        )
        self.assertEqual(foldable["unfolded"]["thickness_range_mm"], [3.9, 4.2])
        self.assertEqual(foldable["unfolded"]["thickness_options_mm"], [])

    def test_weight_alternatives_are_all_preserved(self):
        self.assertEqual(_parse_weight_options("222.5/227.9/229.8 g"), [222.5, 227.9, 229.8])
        self.assertEqual(_parse_weight_options("222 g or 227 g"), [222.0, 227.0])

    def test_region_dependent_nfc_is_explicit(self):
        result = parse_gsmarena_html(
            '<div data-spec="modelname">Example Phone</div>'
            '<div data-spec="nfc">Yes (market/region dependent)</div>'
        )
        self.assertTrue(result["connectivity"]["has_nfc"])
        self.assertEqual(result["connectivity"]["nfc_availability"], "region_dependent")

    def test_obvious_non_phone_device_types_are_classified(self):
        self.assertEqual(_parse_device_type("Samsung Galaxy Watch8 Classic"), "smartwatch")
        self.assertEqual(_parse_device_type("Xiaomi Black Shark GS3 Ultra"), "smartwatch")
        self.assertEqual(_parse_device_type("Google Pixel C"), "tablet")
        self.assertEqual(_parse_device_type("Google Pixel 10 Pro"), "phone")

    def test_discontinued_status_is_normalized(self):
        html = """
        <div data-spec="modelname">Samsung Galaxy Z TriFold</div>
        <div data-spec="status">Discontinued</div>
        """
        result = parse_gsmarena_html(html)
        self.assertEqual(result["status"], "discontinued")


if __name__ == "__main__":
    unittest.main()
