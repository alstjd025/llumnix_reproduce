"""사용자 메모 읽기·쓰기(sources/notes.py). 대시보드에서 쓰기가 되는 유일한 API다."""
from fastapi import APIRouter, Body, HTTPException

from api import jsonable
from sources import notes

router = APIRouter()


@router.get("/notes")
def all_notes():
    return jsonable(notes.listing())


@router.get("/notes/{kind}/{key}")
def get_note(kind: str, key: str):
    try:
        return jsonable(notes.read(kind, key))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/notes/{kind}/{key}")
def put_note(kind: str, key: str, body: dict = Body(...)):
    try:
        return jsonable(notes.write(kind, key, str(body.get("text", "")), body.get("base_mtime")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except notes.Conflict as e:
        raise HTTPException(409, "다른 곳에서 이 메모가 바뀌었다. 새로 불러온 뒤 다시 저장한다")
