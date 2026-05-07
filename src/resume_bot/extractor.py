from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from .llm import VisionClient
from .types import ResumeExtractionResult


SECTION_HINTS = [
    "教育经历",
    "实习经历",
    "工作经历",
    "项目经历",
    "职业技能",
    "专业技能",
    "自我评价",
]
BULLET_PREFIXES = ("-", "•", "*", "1.", "2.", "3.", "1、", "2、", "3、")
DATE_RANGE_PATTERN = re.compile(r"20\d{2}[./年-]\d{1,2}\s*[-~至]\s*20\d{2}[./年-]\d{1,2}")
LATIN_SPLIT_PATTERN = re.compile(r"\b([A-Za-z])\s+([A-Za-z]{2,})\b")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_preview(text: str, limit: int = 1200) -> str:
    preview = text.strip().replace("\r\n", "\n")
    if len(preview) <= limit:
        return preview
    return preview[:limit].rstrip() + "\n..."


def _vision_provider_label(vision_client: VisionClient | None) -> str:
    if not vision_client:
        return ""
    provider = getattr(vision_client, "provider_name", "") or vision_client.__class__.__name__
    model = getattr(vision_client, "model", "")
    if provider == "openai-compatible" and "dashscope.aliyuncs.com" in getattr(vision_client, "base_url", ""):
        provider = "aliyun-bailian-compatible"
    return f"{provider}:{model}" if model else provider


def _route_fields(
    *,
    route_name: str,
    route_summary: str,
    route_reason: str,
    provider_used: str,
) -> dict:
    return {
        "route_name": route_name,
        "route_summary": route_summary,
        "route_reason": route_reason,
        "provider_used": provider_used,
    }


def _evaluate_extracted_text(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact_text = text.replace(" ", "").replace("\n", "")
    bullet_lines = sum(1 for line in lines if line.startswith(BULLET_PREFIXES))
    section_hits = sum(1 for heading in SECTION_HINTS if heading in text)
    date_hits = len(DATE_RANGE_PATTERN.findall(text))
    chinese_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    score = 0
    flags: list[str] = []

    if len(compact_text) >= 1200:
        score += 30
    elif len(compact_text) >= 500:
        score += 20
    elif len(compact_text) >= 180:
        score += 10
    else:
        flags.append("文本长度偏短")

    if len(lines) >= 25:
        score += 20
    elif len(lines) >= 12:
        score += 12
    elif len(lines) >= 6:
        score += 6
    else:
        flags.append("有效行数偏少")

    if section_hits >= 4:
        score += 25
    elif section_hits >= 2:
        score += 15
    elif section_hits == 1:
        score += 8
    else:
        flags.append("缺少简历分段标题")

    if bullet_lines >= 6:
        score += 20
    elif bullet_lines >= 3:
        score += 12
    elif bullet_lines >= 1:
        score += 5
    else:
        flags.append("缺少职责条目")

    if date_hits >= 3:
        score += 10
    elif date_hits >= 1:
        score += 5
    else:
        flags.append("时间范围识别较少")

    if chinese_chars < 80:
        flags.append("中文正文过少")

    should_retry_with_ocr = score < 55 or ("缺少职责条目" in flags and "缺少简历分段标题" in flags)
    return {
        "char_count": len(compact_text),
        "line_count": len(lines),
        "section_hits": section_hits,
        "bullet_lines": bullet_lines,
        "date_hits": date_hits,
        "quality_score": min(score, 100),
        "quality_flags": flags,
        "should_retry_with_ocr": should_retry_with_ocr,
        "preview": _build_preview(text),
    }


def _normalize_extracted_text(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = LATIN_SPLIT_PATTERN.sub(lambda match: match.group(1) + match.group(2), text)
    lines = [line.rstrip() for line in text.split("\n")]
    repaired: list[str] = []
    headings = set(SECTION_HINTS)
    hard_line_prefixes = ("电话", "邮箱", "城市", "性别", "民族", "政治面貌", "婚姻状况", "出生年月")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if repaired and repaired[-1] != "":
                repaired.append("")
            continue
        if not repaired:
            repaired.append(line)
            continue
        previous = repaired[-1]
        if previous == "":
            repaired.append(line)
            continue
        should_join = (
            line not in headings
            and previous not in headings
            and not DATE_RANGE_PATTERN.search(previous)
            and not DATE_RANGE_PATTERN.search(line)
            and "|" not in previous
            and not previous.startswith(hard_line_prefixes)
            and not line.startswith(hard_line_prefixes)
            and not previous.endswith(("。", "；", "！", "？", "：", ":", "|"))
            and not line.startswith(BULLET_PREFIXES)
            and len(previous) >= 10
            and len(line) >= 2
        )
        if should_join:
            repaired[-1] = previous + line
        else:
            repaired.append(line)
    normalized = "\n".join(item for item in repaired if item is not None)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    texts = []
    for page in reader.pages:
        texts.append(page.extract_text() or "")
    return _normalize_extracted_text("\n".join(texts).strip())


def _extract_docx_text(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    return _normalize_extracted_text("\n".join(paragraphs))


def _ocr_images(path: Path, vision_client: VisionClient) -> str:
    prompt = (
        "请严格做 OCR，把简历里能读到的文字尽量完整提取出来。"
        "不要总结，不要解释，只输出识别后的纯文本。"
    )
    return _normalize_extracted_text(vision_client.extract_text(prompt, [path]))


def _ocr_pdf_with_rendering(path: Path, vision_client: VisionClient) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("扫描版 PDF 需要 PyMuPDF 才能渲染后做 OCR") from exc

    image_paths: list[Path] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        doc = fitz.open(str(path))
        for index, page in enumerate(doc[: min(len(doc), 3)]):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = Path(temp_dir) / f"resume_page_{index + 1}.png"
            pix.save(str(image_path))
            image_paths.append(image_path)
        prompt = (
            "请严格做 OCR，把这几页简历里能读到的文字尽量完整提取出来。"
            "不要总结，不要解释，只输出识别后的纯文本。"
        )
        return _normalize_extracted_text(vision_client.extract_text(prompt, image_paths))


def extract_resume_text(path: Path, vision_client: VisionClient | None = None) -> ResumeExtractionResult:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        attempts: list[dict] = []
        chosen_text = ""
        chosen_method = "pdf_text"
        chosen_backend = "pypdf"
        fallback_used = False

        try:
            pdf_text = _extract_pdf_text(path)
        except Exception as exc:
            pdf_text = ""
            attempts.append({"method": "pdf_text", "parser_backend": "pypdf", "error": str(exc)})
        else:
            quality = _evaluate_extracted_text(pdf_text)
            attempts.append(
                {
                    "method": "pdf_text",
                    "parser_backend": "pypdf",
                    **quality,
                }
            )
            chosen_text = pdf_text

        pdf_attempt = attempts[-1] if attempts else {}
        if vision_client:
            need_ocr = not chosen_text or pdf_attempt.get("should_retry_with_ocr", False)
            if need_ocr:
                try:
                    ocr_text = _ocr_pdf_with_rendering(path, vision_client)
                except Exception as exc:
                    attempts.append({"method": "ocr_pdf", "parser_backend": "vision_ocr", "error": str(exc)})
                else:
                    ocr_quality = _evaluate_extracted_text(ocr_text)
                    attempts.append(
                        {
                            "method": "ocr_pdf",
                            "parser_backend": "vision_ocr",
                            **ocr_quality,
                        }
                    )
                    chosen_quality = _evaluate_extracted_text(chosen_text) if chosen_text else {"quality_score": -1}
                    if (
                        not chosen_text
                        or ocr_quality["quality_score"] >= chosen_quality["quality_score"] + 8
                        or pdf_attempt.get("should_retry_with_ocr", False)
                    ):
                        chosen_text = ocr_text
                        chosen_method = "ocr_pdf"
                        chosen_backend = "vision_ocr"
                        fallback_used = bool(pdf_attempt)

        final_quality = _evaluate_extracted_text(chosen_text)
        route_reason = "先直接抽 PDF 文本"
        provider_used = chosen_backend
        route_name = "pdf_text"
        route_summary = "PDF 文本直读"
        if chosen_method == "ocr_pdf":
            route_reason = "检测到 PDF 直读质量不够，自动回退到 OCR"
            provider_used = _vision_provider_label(vision_client)
            route_name = "pdf_ocr"
            route_summary = "PDF OCR 识别"
        return ResumeExtractionResult(
            file_name=path.name,
            file_type=suffix.lstrip("."),
            raw_text=chosen_text,
            extraction_method=chosen_method,
            parser_backend=chosen_backend,
            **_route_fields(
                route_name=route_name,
                route_summary=route_summary,
                route_reason=route_reason,
                provider_used=provider_used,
            ),
            quality_score=final_quality["quality_score"],
            quality_flags=final_quality["quality_flags"],
            fallback_used=fallback_used,
            attempts=attempts,
            raw_text_preview=final_quality["preview"],
        )
    if suffix == ".docx":
        text = _extract_docx_text(path)
        quality = _evaluate_extracted_text(text)
        return ResumeExtractionResult(
            file_name=path.name,
            file_type=suffix.lstrip("."),
            raw_text=text,
            extraction_method="docx_text",
            parser_backend="python-docx",
            **_route_fields(
                route_name="docx_text",
                route_summary="DOCX 文本直读",
                route_reason="Word 文档直接抽取正文",
                provider_used="python-docx",
            ),
            quality_score=quality["quality_score"],
            quality_flags=quality["quality_flags"],
            raw_text_preview=quality["preview"],
        )
    if suffix in {".txt", ".md"}:
        text = _normalize_extracted_text(path.read_text(encoding="utf-8"))
        quality = _evaluate_extracted_text(text)
        return ResumeExtractionResult(
            file_name=path.name,
            file_type=suffix.lstrip("."),
            raw_text=text,
            extraction_method="plain_text",
            parser_backend="filesystem",
            **_route_fields(
                route_name="plain_text",
                route_summary="纯文本直读",
                route_reason="文本文件不需要 OCR",
                provider_used="filesystem",
            ),
            quality_score=quality["quality_score"],
            quality_flags=quality["quality_flags"],
            raw_text_preview=quality["preview"],
        )
    if suffix in {".jpg", ".jpeg", ".png"}:
        if not vision_client:
            raise RuntimeError("图片简历需要配置视觉/OCR 模型")
        text = _ocr_images(path, vision_client)
        quality = _evaluate_extracted_text(text)
        return ResumeExtractionResult(
            file_name=path.name,
            file_type=suffix.lstrip("."),
            raw_text=text,
            extraction_method="image_ocr",
            parser_backend="vision_ocr",
            **_route_fields(
                route_name="image_ocr",
                route_summary="图片 OCR 识别",
                route_reason="图片简历只能走 OCR / 视觉模型",
                provider_used=_vision_provider_label(vision_client),
            ),
            quality_score=quality["quality_score"],
            quality_flags=quality["quality_flags"],
            raw_text_preview=quality["preview"],
        )
    raise RuntimeError(f"暂不支持的简历格式: {suffix}")


def extract_text_from_file(path: Path, vision_client: VisionClient | None = None) -> str:
    return extract_resume_text(path, vision_client=vision_client).raw_text
