from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import select

from .. import forms, worker
from ..deps import FormDep, SessionDep
from ..models import ActivityEntry, Profile, Workflow
from ..templating import templates

router = APIRouter(prefix="/workflows")

NEW_WORKFLOW_DEFAULTS = {"name": "", "profile_id": "", "source_path": "", "dest_path": "",
                         "recursive": True, "enabled": True}


def _form_page(request: Request, session, values: dict, workflow_id: int | None = None,
               errors: list[str] | None = None, status_code: int = 200):
    profiles = session.exec(select(Profile).order_by(Profile.name)).all()
    return templates.TemplateResponse(request, "workflow_form.html", {
        "values": values, "workflow_id": workflow_id, "profiles": profiles,
        "errors": errors or [],
    }, status_code=status_code)


def _get_or_404(session, workflow_id: int) -> Workflow:
    workflow = session.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found")
    return workflow


@router.get("")
def list_workflows(request: Request, session: SessionDep):
    workflows = session.exec(select(Workflow).order_by(Workflow.name)).all()
    return templates.TemplateResponse(request, "workflows.html", {"workflows": workflows})


@router.get("/{workflow_id}/files")
def workflow_files(request: Request, workflow_id: int, session: SessionDep):
    """Which files in the source folder get picked up, and why (not)."""
    workflow = _get_or_404(session, workflow_id)
    return templates.TemplateResponse(request, "workflow_files.html", {
        "workflow": workflow, "report": worker.source_report(workflow),
    })


@router.post("/{workflow_id}/files/retry")
def retry_files(workflow_id: int, form: FormDep, session: SessionDep):
    """Try failed files again at the next scan: one (`name`), or all when no name is given."""
    workflow = _get_or_404(session, workflow_id)
    worker.retry_failed(workflow.id, workflow.source_path, form.get("name") or None)
    return RedirectResponse(f"/workflows/{workflow_id}/files", status_code=303)


@router.get("/new")
def new_workflow_form(request: Request, session: SessionDep):
    return _form_page(request, session, NEW_WORKFLOW_DEFAULTS)


@router.get("/{workflow_id}/edit")
def edit_workflow_form(request: Request, workflow_id: int, session: SessionDep):
    workflow = _get_or_404(session, workflow_id)
    return _form_page(request, session, workflow.model_dump(), workflow_id)


@router.post("/save")
def save_workflow(request: Request, form: FormDep, session: SessionDep):
    workflow_id = int(form["id"]) if form.get("id", "").isdigit() else None
    workflow = _get_or_404(session, workflow_id) if workflow_id else Workflow()
    data, errors = forms.validate(forms.WorkflowForm, form)
    if data and not session.get(Profile, data.profile_id):
        errors.append("Profile does not exist.")
    if errors:
        return _form_page(request, session, form, workflow_id, errors, status_code=400)
    for key, value in data.model_dump().items():
        setattr(workflow, key, value)
    session.add(workflow)
    session.commit()
    return RedirectResponse("/workflows", status_code=303)


@router.post("/{workflow_id}/delete")
def delete_workflow(workflow_id: int, session: SessionDep):
    workflow = _get_or_404(session, workflow_id)
    # Keep the activity, just detach it (it carries a copy of the name).
    for entry in session.exec(select(ActivityEntry).where(ActivityEntry.workflow_id == workflow_id)):
        entry.workflow_id = None
        session.add(entry)
    session.delete(workflow)
    session.commit()
    return RedirectResponse("/workflows", status_code=303)
