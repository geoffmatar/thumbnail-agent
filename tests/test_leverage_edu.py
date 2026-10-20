import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

# Importing the app must not load a developer's real local credentials.
with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}, clear=True), \
     mock.patch.object(Path, "read_text", return_value=""):
    import thumbnail_agent as agent


def visible_white_pixels(image):
    return sum(
        count for count, pixel in image.getcolors(image.width * image.height)
        if min(pixel[:3]) >= 235 and (len(pixel) == 3 or pixel[3] >= 128)
    )


class LeverageEduTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = agent.get_client_config("leverage-edu")
        environment = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        work = mock.patch.object(agent, "WORK_DIR", self.root)
        work.start()
        self.addCleanup(work.stop)
        network = mock.patch.object(
            agent.urllib.request, "urlopen",
            side_effect=AssertionError("Unexpected network request in Leverage Edu offline tests"),
        )
        network.start()
        self.addCleanup(network.stop)
        self.subject = self.root / "subject.png"
        Image.new("RGB", (1080, 1920), "#925732").save(self.subject)
        self.brief = {
            "visual_prompt": "The referenced student celebrates arriving at university.",
            "negative_prompt": "text, watermarks",
            "rationale": "Show the student's university journey.",
        }

    def test_three_explicit_headline_lines_accept_pipes_and_newlines(self):
        expected = ["MY JOURNEY TO", "UNIVERSITY OF", "SALFORD"]
        for title in (
            "My journey to | University of | Salford",
            "My journey to\nUniversity of\nSalford",
            "  My   journey to | University of\n Salford  ",
        ):
            with self.subTest(title=title):
                self.assertEqual(agent.split_title_lines(title, self.config), expected)

    def test_two_part_title_keeps_the_selected_final_phrase_highlighted(self):
        lines = agent.split_title_lines("MY JOURNEY TO UNIVERSITY OF | SALFORD", self.config)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0])
        self.assertTrue(lines[1])
        self.assertEqual(" ".join(lines[:2]), "MY JOURNEY TO UNIVERSITY OF")
        self.assertEqual(lines[2], "SALFORD")

    def test_automatic_wrapping_and_extra_separators_do_not_lose_words(self):
        title = "How I found my dream university in Britain"
        lines = agent.split_title_lines(title, self.config)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(lines))
        self.assertEqual(" ".join(lines), title.upper())
        self.assertEqual(
            agent.split_title_lines("MY JOURNEY | TO UNIVERSITY | OF | SALFORD", self.config),
            ["MY JOURNEY", "TO UNIVERSITY", "OF SALFORD"],
        )

    def test_short_headlines_keep_the_final_phrase_in_the_highlighted_line(self):
        for title, expected in (
            ("Salford", ["", "", "SALFORD"]),
            ("Dream University", ["", "DREAM", "UNIVERSITY"]),
            ("My | Salford", ["", "MY", "SALFORD"]),
        ):
            with self.subTest(title=title):
                lines = agent.split_title_lines(title, self.config)
                self.assertEqual(lines, expected)
                self.assertEqual(" ".join(line for line in lines if line).split(), title.replace("|", " ").upper().split())

    def test_existing_brands_keep_their_two_line_headlines_and_focus_zone(self):
        for slug in ("zoomex", "alliance-latin", "alliance-black", "alliance-lgbtq", "siyata"):
            with self.subTest(client=slug):
                config = agent.get_client_config(slug)
                self.assertEqual(
                    agent.split_title_lines("MY JOURNEY TO | UNIVERSITY OF SALFORD", config),
                    ["MY JOURNEY TO", "UNIVERSITY OF SALFORD"],
                )
                self.assertEqual(
                    agent.split_title_lines("THE KID WHO REFUSED TO QUIT", config),
                    ["THE KID WHO", "REFUSED TO QUIT"],
                )
                self.assertEqual(agent.client_focus_zone(config), {"x": 210, "y": 560, "w": 660, "h": 660})

    def test_clean_template_has_no_visible_sample_headline_letters(self):
        with Image.open(self.config["design_path"]) as design:
            self.assertEqual(design.size, (1080, 1920))
            # The template's logo is above this region. Its original sample
            # headline was white, so a blank lower panel must contain no white
            # lettering before dynamic titles are drawn.
            headline = design.convert("RGBA").crop((150, 1040, 940, 1460))
            self.assertEqual(visible_white_pixels(headline), 0, "The bundled template still contains sample headline lettering")

    def test_render_draws_all_three_white_lines_and_preserves_brand_artwork(self):
        output = self.root / "leverage.png"
        # Uniform black stays black through the subject's tone adjustments,
        # giving a known backdrop for the template's translucent artwork.
        Image.new("RGB", (1080, 1920), "black").save(self.subject)
        agent.render_thumbnail("MY JOURNEY TO | UNIVERSITY OF | OXFORD", self.subject, output, self.config)
        with Image.open(output) as rendered, Image.open(self.config["design_path"]) as design:
            self.assertEqual(rendered.size, (1080, 1920))
            result = rendered.convert("RGB")
            for line, (top, bottom) in enumerate(((1066, 1162), (1193, 1289), (1311, 1439)), 1):
                white_pixels = visible_white_pixels(result.crop((188, top, 892, bottom)))
                self.assertGreater(white_pixels, 100, f"Headline line {line} is missing")
            original = design.convert("RGBA")
            for point in ((200, 500), (100, 1600), (540, 1800)):
                red, green, blue, alpha = original.getpixel(point)
                expected = tuple((channel * alpha + 127) // 255 for channel in (red, green, blue))
                self.assertEqual(result.getpixel(point), expected)

    def test_last_line_is_dynamic_white_text_on_the_exact_cyan_highlight(self):
        first = Image.new("RGBA", (1080, 1920), (25, 35, 55, 255))
        second = first.copy()
        agent.draw_title(first, "MY JOURNEY TO | UNIVERSITY OF | OXFORD", self.config)
        agent.draw_title(second, "MY JOURNEY TO | UNIVERSITY OF | CAMBRIDGE", self.config)
        # Start on a plain canvas so a cyan rectangle already burned into a
        # template cannot make this check pass when draw_title stops painting it.
        self.assertEqual(first.getpixel((190, 1313)), (84, 188, 200, 255))
        self.assertEqual(second.getpixel((190, 1313)), (84, 188, 200, 255))
        self.assertNotEqual(
            first.crop((188, 1311, 893, 1440)).tobytes(),
            second.crop((188, 1311, 893, 1440)).tobytes(),
            "Changing the final headline did not update the highlighted line",
        )
        self.assertEqual(first.crop((188, 1066, 893, 1290)).tobytes(), second.crop((188, 1066, 893, 1290)).tobytes())

    def test_leverage_focus_bounds_reach_planning_generation_and_reference_editing(self):
        self.assertEqual(agent.client_focus_zone(self.config), {"x": 400, "y": 460, "w": 640, "h": 510})
        focus_prompt = agent.focus_zone_prompt(self.config)
        self.assertIn("from x=400 to 1040, y=460 to 970", focus_prompt)
        self.assertNotIn("top half", focus_prompt.lower())
        self.assertIn("face above y=460", agent.safe_zone_negative_prompt(self.config))
        self.assertIn("x=400..1040, y=460..970", agent.person_reference_prompt(self.config))
        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(self.brief)}) as responses:
            agent.create_visual_brief(
                "The student arrives at university.", "MY JOURNEY TO | UNIVERSITY OF | OXFORD", self.config,
                person_reference_path=self.subject,
            )
        payload = responses.call_args.args[0]
        developer = payload["input"][0]["content"]
        user_text = payload["input"][1]["content"][0]["text"]
        self.assertIn("x=400..1040", developer)
        self.assertIn("y=460..970", developer)
        self.assertIn("from x=400 to 1040, y=460 to 970", user_text)
        for prompt in (developer, user_text):
            self.assertIn("to the right of the Leverage Edu logo", prompt)
            self.assertNotIn("y=560", prompt)
            self.assertNotIn("y=1220", prompt)
        encoded = io.BytesIO()
        Image.new("RGB", (512, 768), "#925732").save(encoded, "PNG")
        for with_reference in (False, True):
            with self.subTest(reference=with_reference):
                with mock.patch.object(agent, "openai_images_generate", return_value=encoded.getvalue()) as generate, \
                     mock.patch.object(agent, "openai_images_edit", return_value=encoded.getvalue()) as edit:
                    agent.generate_subject_image(self.brief, self.config, self.subject if with_reference else None)
                active, inactive = (edit, generate) if with_reference else (generate, edit)
                active.assert_called_once()
                inactive.assert_not_called()
                prompt = active.call_args.args[0]
                self.assertIn("x=400..1040, y=460..970", prompt)
                self.assertIn("to the right of the Leverage Edu logo", prompt)
                self.assertNotIn("y=560", prompt)
                self.assertNotIn("y=1220", prompt)

    def test_public_status_reports_the_bundled_leverage_assets_ready(self):
        status = agent.public_client_status(self.config)
        self.assertEqual(status["slug"], "leverage-edu")
        self.assertEqual(status["display_name"], "Leverage Edu")
        self.assertTrue(status["design_ready"], "Leverage Edu template is missing")
        self.assertTrue(status["font_ready"], "The actual Gagalin font must be bundled before the agent is ready")


if __name__ == "__main__":
    unittest.main()
