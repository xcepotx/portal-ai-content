from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from google import genai
from google.genai import types
from PIL import Image


@dataclass
class NanoBananaResult:
    images: List[Image.Image]
    texts: List[str]


class NanoBananaClient:
    """
    Nano Banana:
      - gemini-2.5-flash-image
    Nano Banana Pro:
      - gemini-3-pro-image-preview
    """

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = genai.Client(api_key=api_key)

    def generate(
        self,
        prompt: str,
        ref_images: Optional[List[Image.Image]] = None,
        aspect_ratio: str = "1:1",
        image_size: Optional[str] = None,  # "1K","2K","4K" (Pro)
    ) -> NanoBananaResult:
        contents = [prompt]
        if ref_images:
            contents.extend(ref_images)

        cfg = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=image_size,
            ),
        )

        resp = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=cfg,
        )

        images: List[Image.Image] = []
        texts: List[str] = []

        for part in resp.parts:
            if part.text is not None:
                texts.append(part.text)
            else:
                img = part.as_image()
                if img is not None:
                    images.append(img)

        return NanoBananaResult(images=images, texts=texts)
