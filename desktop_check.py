"""Regression checks run inside the frozen EXE, without a real API key."""

import asyncio
import json
import tempfile
import traceback
import unicodedata
from pathlib import Path
from types import SimpleNamespace


def run_checks(report: Path, translation: bool = False) -> int:
    result = {"ok": False, "checks": []}
    report.parent.mkdir(parents=True, exist_ok=True)
    app = None
    try:
        # Import exactly the pipeline which failed only after pressing Start.
        from babeldoc.format.pdf.high_level import async_translate
        from babeldoc.docvision.doclayout import DocLayoutModel
        from babeldoc.translator.translator import BaseTranslator, OpenAITranslator
        from babeldoc.format.pdf.translation_config import TranslationConfig
        from bitstring import BitStream
        import tiktoken_ext.openai_public
        assert BitStream(bin="1010").uint == 10
        result["checks"].append("frozen_translation_imports")
        from babeldoc.asynchronize import AsyncCallback
        async def check_finish():
            callback = AsyncCallback()
            producer = asyncio.create_task(asyncio.to_thread(callback.finished_callback, type="finish"))
            async def consume():
                return [event.kwargs["type"] async for event in callback]
            assert await asyncio.wait_for(consume(), 3) == ["finish"]
            await producer
        asyncio.run(check_finish())
        result["checks"].append("translation_completion_does_not_hang")

        if translation:
            import pymupdf
            from babeldoc.format.pdf.translation_config import WatermarkOutputMode

            class SampleTranslator(BaseTranslator):
                name = "desktop_check"
                model = "local-sample"

                def do_translate(self, text, rate_limit_params=None):
                    return "这是一份用于检查论文翻译和中文排版的示例文档。"

                def do_llm_translate(self, text, rate_limit_params=None):
                    raise NotImplementedError

            with tempfile.TemporaryDirectory(prefix="zhijian-pdf-", ignore_cleanup_errors=True) as folder:
                source = Path(folder) / "sample.pdf"
                with pymupdf.open() as doc:
                    page = doc.new_page()
                    page.insert_text((72, 90), "A sample scientific paper", fontsize=18)
                    page.insert_textbox((72, 130, 500, 300),
                                        "This sample document describes a scientific method. " * 12, fontsize=12)
                    doc.save(source)
                translator = SampleTranslator("en", "zh", True)
                config = TranslationConfig(translator=translator, input_file=str(source),
                                           lang_in="en", lang_out="zh", output_dir=folder,
                                           doc_layout_model=DocLayoutModel.load_onnx(),
                                           auto_extract_glossary=False, use_rich_pbar=False,
                                           watermark_output_mode=WatermarkOutputMode.NoWatermark)
                async def process():
                    completed = None
                    async for event in async_translate(config):
                        if event["type"] == "error":
                            raise RuntimeError(str(event["error"]))
                        if event["type"] == "finish":
                            completed = event["translate_result"]
                    assert completed is not None, "No output result"
                    for path in (completed.mono_pdf_path, completed.dual_pdf_path):
                        assert path and Path(path).is_file(), "PDF not written"
                        with pymupdf.open(path) as pdf:
                            assert len(pdf) > 0
                            content = "".join(page.get_text() for page in pdf)
                            assert "示例文档" in unicodedata.normalize("NFKC", "".join(content.split())), repr(content[:300])
                asyncio.run(process())
                result["checks"].append("sample_pdf_to_chinese_and_bilingual_pdf_no_paid_api")
        else:
            from desktop_app import TranslatorApp
            from desktop_settings import desktop_directory, load_settings
            from tkinterdnd2 import COPY, REFUSE_DROP
            with tempfile.TemporaryDirectory(prefix="zhijian-ui-") as folder:
                settings = Path(folder) / "settings.json"
                app = TranslatorApp(settings_file=settings)
                app.root.update()
                assert Path(app.output_path.get()) == desktop_directory()
                result["checks"].append("windows_desktop_default")
                default_geometry = app.root.geometry()
                for geometry in (default_geometry, "720x460", "920x640"):
                    app.root.geometry(geometry)
                    app.root.update()
                    assert app.action.winfo_ismapped(), "Start button not visible"
                    assert app.action.winfo_rooty() + app.action.winfo_height() <= app.root.winfo_rooty() + app.root.winfo_height()
                    assert app.action.winfo_rootx() + app.action.winfo_width() <= app.root.winfo_rootx() + app.root.winfo_width()
                result["checks"].append("start_button_default_and_small_windows")
                source = Path(folder) / "中文 paper with spaces.pdf"
                source.write_bytes(b"%PDF-1.4")
                app.output_path.set(folder)
                # TkDND must load in the frozen EXE and bind its native event.
                drop_data = "{" + str(source) + "}"
                script = app.root.tk.call("bind", app.file_entry._w, "<<Drop>>")
                assert script, "DnD event binding missing"
                assert app.root.tk.call("package", "present", "tkdnd")
                assert app._drop_pdf(SimpleNamespace(data=drop_data)) == COPY
                assert Path(app.pdf_path.get()) == source
                assert app.output_path.get() == folder
                assert app._drop_pdf(SimpleNamespace(data=drop_data + " " + drop_data)) == REFUSE_DROP
                result["checks"].append("tkdnd_loaded_bound_unicode_space_path_selection")
                sample_key = "self-check-key-not-a-real-credential"
                app.api_key.set(sample_key)
                assert app._save_settings()
                assert sample_key not in settings.read_text(encoding="utf-8")
                app.root.destroy()
                app = TranslatorApp(settings_file=settings)
                assert app.api_key.get() == sample_key
                app._clear_key()
                assert not load_settings(settings).get("api_key")
                result["checks"].append("dpapi_persistence_reload_and_clear")
                app.root.destroy()
                app = None
        result["ok"] = True
    except BaseException:
        result["error"] = traceback.format_exc()
    finally:
        if app is not None:
            app.root.destroy()
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
