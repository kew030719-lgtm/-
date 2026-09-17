from __future__ import annotations

import base64

import httpx
from openai import AsyncOpenAI

from .config import Settings
from .resume import ResumeError

MAX_VISION_PAGES = 8


def render_pdf_pages(data: bytes) -> list[bytes]:
    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
        try:
            if document.page_count > MAX_VISION_PAGES:
                raise ResumeError(f"扫描版 PDF 最多支持 {MAX_VISION_PAGES} 页")
            pages = []
            for page in document:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                pages.append(pixmap.tobytes("png"))
            return pages
        finally:
            document.close()
    except ResumeError:
        raise
    except Exception as exc:
        raise ResumeError("扫描版 PDF 页面渲染失败") from exc


class VisionResumeReader:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def extract(self, data: bytes) -> str:
        if not self.settings.vision_model_name:
            raise ResumeError("这是扫描版 PDF，请先在模型设置中填写支持图片输入的视觉模型名称")
        if not self.settings.api_key:
            raise ResumeError("尚未配置模型密钥，无法识别扫描版 PDF")
        pages = render_pdf_pages(data)
        if not pages:
            raise ResumeError("扫描版 PDF 没有可识别页面")
        content: list[dict[str, object]] = [{
            "type": "text",
            "text": (
                "请逐页识别这些简历图片中的全部文字。保持姓名、联系方式、学校、公司、项目、"
                "日期、数字和技术名词原样，不要总结、改写、补全或推断。按页面顺序输出纯文本，"
                "不同栏目换行，不要使用 Markdown 代码块。"
            ),
        }]
        for page in pages:
            encoded = base64.b64encode(page).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"},
            })
        http_client = httpx.AsyncClient(trust_env=False, timeout=180)
        try:
            client = AsyncOpenAI(
                base_url=self.settings.model_base_url, api_key=self.settings.api_key,
                max_retries=1, http_client=http_client,
            )
            response = await client.chat.completions.create(
                model=self.settings.vision_model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0, max_tokens=8000,
            )
        except Exception as exc:
            raise ResumeError(f"视觉模型识别扫描版 PDF 失败：{str(exc).strip() or type(exc).__name__}") from exc
        finally:
            await http_client.aclose()
        text = response.choices[0].message.content or ""
        if not isinstance(text, str) or len(text.strip()) < 30:
            raise ResumeError("视觉模型没有返回足够的简历文字")
        return text
