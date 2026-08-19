"""Knowledge-management API for CRM clients, machines, and documents."""

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel

from auth import get_current_user
from knowledge_service import (
    create_uploaded_document,
    delete_document,
    get_document,
    list_client_machines,
    list_clients,
    list_documents,
    queue_health,
    retry_document,
    set_client_active,
    set_machine_selected,
)


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class ClientStateRequest(BaseModel):
    active: bool


class MachineStateRequest(BaseModel):
    selected: bool


@router.get("/clients")
def clients(
    q: str = Query("", max_length=200),
    include_inactive: bool = False,
    user: dict = Depends(get_current_user),
):
    return list_clients(user, q=q, include_inactive=include_inactive)


@router.patch("/clients/{client_id}")
def update_client(client_id: str, req: ClientStateRequest, user: dict = Depends(get_current_user)):
    return set_client_active(client_id, req.active, user)


@router.get("/clients/{client_id}/machines")
def client_machines(
    client_id: str,
    q: str = Query("", max_length=200),
    user: dict = Depends(get_current_user),
):
    return list_client_machines(client_id, user, q=q)


@router.patch("/clients/{client_id}/machines/{machine_id}")
def update_machine(
    client_id: str,
    machine_id: str,
    req: MachineStateRequest,
    user: dict = Depends(get_current_user),
):
    return set_machine_selected(client_id, machine_id, req.selected, user)


@router.get("/machines/{machine_id}/documents")
def machine_documents(machine_id: str, user: dict = Depends(get_current_user)):
    return list_documents(machine_id, user)


@router.post("/machines/{machine_id}/documents", status_code=201)
async def upload_document(
    machine_id: str,
    category: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    return await create_uploaded_document(machine_id, category, file, user)


@router.get("/documents/{document_id}")
def document(document_id: str, user: dict = Depends(get_current_user)):
    return get_document(document_id, user)


@router.post("/documents/{document_id}/retry")
def retry(document_id: str, user: dict = Depends(get_current_user)):
    return retry_document(document_id, user)


@router.delete("/documents/{document_id}")
def remove(document_id: str, user: dict = Depends(get_current_user)):
    deleted = delete_document(document_id, user)
    if deleted:
        return Response(status_code=204)
    return Response(status_code=202)


@router.get("/health")
def health(user: dict = Depends(get_current_user)):
    if user.get("role") != "superadmin":
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Superadmin access required")
    return queue_health()
