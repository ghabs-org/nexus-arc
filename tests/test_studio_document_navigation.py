"""Document previews navigate immutable source units without cloud dependencies."""

import base64
import io
import subprocess
import zipfile
from pathlib import Path

import pytest

from nexus.core.studio import StudioService


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def save(studio, content, filename, **values):
    return studio.catalog.save(
        "artifacts",
        {
            "name": filename,
            **values,
            "data": {"filename": filename, "content_base64": base64.b64encode(content).decode()},
        },
    )


def office(*sections):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for number, text in enumerate(sections, 1):
            archive.writestr(
                f"ppt/slides/slide{number}.xml",
                f'<slide xmlns:a="urn:test"><a:t>{text}</a:t></slide>',
            )
    return target.getvalue()


def pdf(pages=1):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for number in range(1, pages + 1):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td (Evidence page {number}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


def test_office_navigation_preserves_pinned_revision_after_replacement(studio):
    original = save(studio, office("Original source", "Second source"), "source.pptx")
    save(studio, office("Replacement"), "source.pptx", id=original["id"], revision=1)
    pinned = studio.artifacts.revision(original["id"], 1)
    preview = studio.artifacts.preview(pinned, page=2)
    assert preview == {
        "kind": "office",
        "artifact_id": original["id"],
        "revision": 1,
        "filename": "source.pptx",
        "page": 2,
        "pages": 2,
        "unit": "Section",
        "extraction_available": True,
        "text": "Second source",
        "truncated": False,
    }
    with pytest.raises(ValueError, match="outside"):
        studio.artifacts.preview(studio.artifacts.revision(original["id"]), page=2)


def test_pdf_navigation_can_reach_exact_page_beyond_bounded_full_text_excerpt(studio):
    record = save(studio, pdf(51), "evidence.pdf")
    excerpt = studio.artifacts.pdf_text(studio.artifacts.get(record))
    assert len(excerpt["page_texts"]) == 50 and excerpt["truncated"]
    selected = studio.artifacts.preview(record, page=51)
    assert selected["pages"] == 51 and selected["page"] == 51
    assert selected["unit"] == "Page" and selected["text"] == "Evidence page 51"
    assert selected["extraction_available"] and not selected["truncated"]
    with pytest.raises(ValueError, match="outside"):
        studio.artifacts.preview(record, page=52)


def test_archived_source_retains_pinned_preview_but_cannot_be_new_attachment(studio):
    record = save(studio, office("Pinned source"), "source.pptx")
    studio.catalog.archive("artifacts", record["id"], {"revision": 1})
    pinned = studio.artifacts.revision(record["id"], 1)
    assert studio.artifacts.preview(pinned)["text"] == "Pinned source"
    with pytest.raises(ValueError, match="archived"):
        studio.artifacts.inputs({"artifact_refs": [{"id": record["id"], "revision": 1}]})


@pytest.mark.parametrize("page", [True, 0, -1, 1.5, "2"])
def test_preview_rejects_invalid_page_types_before_storage_access(studio, page):
    with pytest.raises(ValueError, match="positive integer"):
        studio.artifacts.preview({}, page=page)


def test_unavailable_optional_extractor_is_visible_without_provider_fallback(studio, monkeypatch):
    record = save(studio, b"%PDF-invalid", "source.pdf")

    def unavailable(*args, **kwargs):
        raise ValueError("Install nexus-arc[documents] for local PDF extraction")

    monkeypatch.setattr(studio.artifacts, "pdf_text", unavailable)
    preview = studio.artifacts.preview(record)
    assert preview["extraction_available"] is False and preview["revision"] == 1
    assert "nexus-arc[documents]" in preview["message"] and not preview["text"]


def test_office_invalid_container_and_oversized_section_are_bounded(studio):
    invalid = save(studio, b"invalid zip", "invalid.docx")
    assert not studio.artifacts.preview(invalid)["extraction_available"]
    large = save(studio, office("x" * 150000), "large.pptx")
    preview = studio.artifacts.preview(large)
    assert preview["extraction_available"] and preview["truncated"]
    assert len(preview["text"]) == 128000


def test_non_document_page_request_does_not_silently_show_another_position(studio):
    record = save(studio, b"plain text", "source.txt")
    with pytest.raises(ValueError, match="no additional pages"):
        studio.artifacts.preview(record, page=2)


def test_browser_navigation_and_citation_links_keep_exact_revision():
    source = Path(__file__).resolve().parents[1] / "nexus/studio_static/content.js"
    program = r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.listeners={};this.textContent='';this.style={};}
 append(...children){this.children.push(...children);}appendChild(child){this.append(child);}
 replaceChildren(...children){this.children=children;}setAttribute(name,value){this.attributes[name]=value;}
 addEventListener(name,listener){(this.listeners[name]??=[]).push(listener);}querySelector(){return null;}
}
const document={createElement:tag=>new Element(tag)};
const context=vm.createContext({document,window:{},URL,encodeURIComponent,console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.container=new Element('article');context.sources=[{filename:'source.pdf',artifact_id:'a/b',artifact_revision:1,page:51}];
vm.runInContext('showCitations(container,sources)',context);
const link=context.container.children[0].children[1];
assert.equal(link.href,'/artifact.html?id=a%2Fb&revision=1&page=51');
let prevented=false;await link.listeners.click[0]({preventDefault(){prevented=true;}});
assert.equal(prevented,false,'shared surface keeps standalone viewer fallback');
const requests=[];context.api=async path=>{requests.push(path);return {kind:'pdf',artifact_id:'a/b',revision:1,page:51,pages:52,text:'Pinned',extraction_available:true};};
context.showDetail=()=>{};context.$=()=>new Element('div');
await link.listeners.click[0]({preventDefault(){prevented=true;}});
assert.equal(requests[0],'/api/catalog/artifacts/a%2Fb/preview?revision=1&page=51');assert.equal(prevented,true);
context.preview={kind:'pdf',artifact_id:'a/b',revision:1,page:51,pages:52,text:'Pinned',unit:'Page',extraction_available:true};
let wrongRevision=false;const navigated=[];context.options={fetchPage:async page=>{navigated.push(page);return {...context.preview,revision:wrongRevision?2:1,page,text:wrongRevision?'Wrong revision':'Exact page'};}};
const root=vm.runInContext('previewContent(preview,options)',context),controls=root.children[0];
await controls.children[4].listeners.click[0]();assert.deepEqual(navigated,[52]);
assert.equal(root.children[2].textContent,'Exact page');assert.equal(controls.children[4].disabled,true);
wrongRevision=true;
await controls.children[0].listeners.click[0]();
assert.equal(root.children[2].textContent,'Exact page');assert.match(root.children[1].textContent,/revision changed/);
assert.equal(root.children[4].href,'/api/catalog/artifacts/a%2Fb/versions/1');
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", "(async()=>{" + program + "})()", str(source)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
