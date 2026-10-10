# -*- coding: utf-8 -*-
"""
Public About page + admin editing.

mrie is single-user: the About page is a singleton row (AboutPage never has
more than one record - _get_or_create() always operates on "the" row,
creating it with default content on first access). Viewing is public;
editing requires a logged-in session, same as every other write path in this
app (Depends(check_login)).

Uploaded images are stored in the database (AboutImages, one row per image)
and served by GET /about/images/{image_id}, referenced from the stored HTML
by that URL. Not as loose files on disk: keeping every piece of runtime
state inside the one SQLite file means a single file copy (see
`make db-backup`) backs up the whole site, images included. Also not the base64-inlined blob pattern WebLinks uses
for its preview thumbnail (image_preview_data): the rich-text editor can
accumulate many free-form images, and inlining all of them into the
`content` column would bloat every page load. A separate row per image,
fetched by URL, keeps the stored HTML small and lets browsers cache each
image independently.

Author:
    Mike Ryan
    MIT Licensed
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger
from sqlalchemy import Select, insert, update

from ..db_tables import AboutImages, AboutPage
from ..functions.db_guards import is_db_error, safe_record
from ..functions.login_required import check_login
from ..resources import db_ops, templates

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

DEFAULT_CONTENT = """\
<h2>Professional Summary</h2>
<p>Seasoned technology executive with decades of experience leading cloud-first \
transformations and enterprise modernization across highly regulated financial \
services and manufacturing environments. Bringing a hands-on technical \
leadership approach, maintaining direct involvement in system architecture, \
platform reliability, and modernization initiatives while guiding teams \
through complex, large-scale changes. Proven ability to align technology \
strategy with business goals, delivering scalable, resilient platforms that \
reduce costs, improve operational stability, and meet stringent regulatory \
requirements (FINRA, SOX, FDA, ITAR). Adept at building and mentoring \
high-performing teams and driving execution through Agile and DevOps \
practices.</p>
<h2>Skills &amp; Technical Competencies</h2>
<ul>
<li><strong>Programming &amp; Frameworks:</strong> Java, Python, C#, .NET, Angular, React, HTMX</li>
<li><strong>Cloud &amp; Architecture:</strong> AWS, Azure, Cloud-Native Architecture, Serverless, DBaaS, Microservices, Micro Frontends, Docker, Kubernetes</li>
<li><strong>Workflow, BPM &amp; Integration:</strong> Temporal, Pega, Camunda, IBM BPM, Apache Airflow, Kafka</li>
<li><strong>Data Platforms &amp; Analytics:</strong> Oracle, Cassandra, CockroachDB, SQL/NoSQL Datastores, Power BI, Qlik, MicroStrategy, NLP/Text Analytics</li>
<li><strong>DevOps &amp; Engineering Practices:</strong> CI/CD Pipelines, Jenkins, GitHub, Bitbucket, TFS, Jira, SonarQube, Automated Testing, Scrum, Kanban</li>
<li><strong>AI &amp; Machine Learning:</strong> OpenAI, GitHub Copilot, AI-Assisted Development Tools, classification ML APIs (NPL)</li>
<li><strong>Enterprise Content Management:</strong> OpenText, MessagePoint, Documentation Automation, WORM Storage</li>
<li><strong>Regulated Environments:</strong> FINRA, SEC, SOX, FDA, ITAR, ADA</li>
</ul>
"""


async def _get_or_create_about() -> AboutPage:
    page = safe_record(await db_ops.read_one_record(Select(AboutPage)))
    if page is None:
        new_pkid = str(uuid.uuid4())
        result = await db_ops.execute_one(
            insert(AboutPage).values(pkid=new_pkid, content=DEFAULT_CONTENT)
        )
        if is_db_error(result):
            raise RuntimeError(f"Failed to create the About page row: {result}")
        # execute_one() doesn't hand back the inserted row itself (unlike the
        # deprecated create_one() it replaces) - read it back by the pkid we
        # just generated so callers still get a real AboutPage instance.
        page = safe_record(
            await db_ops.read_one_record(
                Select(AboutPage).where(AboutPage.pkid == new_pkid)
            )
        )
    return page


@router.get("")
async def view_about(request: Request):
    page = await _get_or_create_about()
    return templates.TemplateResponse(
        request=request,
        name="about/view.html",
        context={"content": page.content if page else ""},
    )


@router.get("/edit")
async def edit_about_form(request: Request, user_info: dict = Depends(check_login)):
    page = await _get_or_create_about()
    return templates.TemplateResponse(
        request=request,
        name="about/edit.html",
        context={"content": page.content if page else ""},
    )


@router.post("/edit")
async def save_about(request: Request, user_info: dict = Depends(check_login)):
    form = await request.form()
    content = form.get("content", "")
    page = await _get_or_create_about()
    await db_ops.execute_one(
        update(AboutPage).where(AboutPage.pkid == page.pkid).values(content=content)
    )
    logger.info("About page updated")
    return RedirectResponse(url="/about", status_code=303)


@router.post("/upload-image")
async def upload_about_image(
    file_to_upload: UploadFile = File(..., alias="fileToUpload"),
    user_info: dict = Depends(check_login),
):
    if file_to_upload.content_type not in ALLOWED_IMAGE_TYPES:
        return JSONResponse(
            {"success": False, "message": "Unsupported image type"}, status_code=400
        )

    data = await file_to_upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"success": False, "message": "Image is too large (5 MB max)"},
            status_code=413,
        )

    image_id = str(uuid.uuid4())
    result = await db_ops.execute_one(
        insert(AboutImages).values(
            pkid=image_id, content_type=file_to_upload.content_type, data=data
        )
    )
    if is_db_error(result):
        logger.error(f"Error saving uploaded about-page image {image_id}: {result}")
        return JSONResponse(
            {"success": False, "message": "Failed to save the uploaded image"},
            status_code=500,
        )

    logger.info(f"About page image uploaded: {image_id}")
    return JSONResponse({"success": True, "url": f"/about/images/{image_id}"})


@router.get("/images/{image_id}", responses={404: {"description": "No such image"}})
async def get_about_image(image_id: str):
    image = safe_record(
        await db_ops.read_one_record(
            Select(AboutImages).where(AboutImages.pkid == image_id)
        )
    )
    if image is None:
        raise HTTPException(status_code=404)
    # Each upload gets a fresh id and rows are never rewritten, so a given
    # URL's bytes never change - safe to let browsers cache it forever.
    # nosniff because content_type came from the uploading client.
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
