"""NAVER API HUB 뉴스 검색과 검색어 트렌드 호출을 한곳에서 관리한다."""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import httpx

from app.config import (
    NAVER_API_HUB_CLIENT_ID,
    NAVER_API_HUB_CLIENT_SECRET,
    NAVER_API_HUB_ENABLED,
)


class NaverApiHubUnavailable(RuntimeError):
    """NAVER API HUB를 사용하도록 설정하지 않았을 때 발생한다."""


class NaverApiHubClient:
    """환경 변수로만 인증 정보를 받는 NAVER API HUB 클라이언트."""

    NEWS_ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/news"
    IMAGE_ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/image"
    SEARCH_TREND_ENDPOINT = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = (client_id if client_id is not None else NAVER_API_HUB_CLIENT_ID).strip()
        self.client_secret = (
            client_secret if client_secret is not None else NAVER_API_HUB_CLIENT_SECRET
        ).strip()
        if not NAVER_API_HUB_ENABLED or not self.client_id or not self.client_secret:
            raise NaverApiHubUnavailable(
                "NAVER_API_HUB_ENABLED=true 및 NAVER_API_HUB_CLIENT_ID/"
                "NAVER_API_HUB_CLIENT_SECRET 설정이 필요합니다"
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
        }

    def search_news(
        self,
        query: str,
        *,
        display: int = 10,
        start: int = 1,
        sort: str = "date",
    ) -> dict[str, Any]:
        """뉴스 검색 결과를 JSON으로 반환한다."""
        normalized_query = " ".join((query or "").split())
        if not normalized_query:
            raise ValueError("뉴스 검색어는 비어 있을 수 없습니다")
        if not 1 <= int(display) <= 100:
            raise ValueError("display는 1~100이어야 합니다")
        if not 1 <= int(start) <= 1000:
            raise ValueError("start는 1~1000이어야 합니다")
        if sort not in {"sim", "date"}:
            raise ValueError("sort는 sim 또는 date여야 합니다")
        response = httpx.get(
            self.NEWS_ENDPOINT,
            params={
                "query": normalized_query,
                "display": int(display),
                "start": int(start),
                "sort": sort,
                "format": "json",
            },
            headers=self._headers,
            timeout=12,
            follow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("NAVER 뉴스 API 응답 형식이 올바르지 않습니다")
        return body

    def search_images(
        self,
        query: str,
        *,
        display: int = 10,
        start: int = 1,
        sort: str = "sim",
        filter: str = "all",
    ) -> dict[str, Any]:
        """이미지 검색 결과를 JSON으로 반환한다.

        2026-10-06 사용자 요청: 대본에 등장하는 구체적 소품(예: "전역모")의
        실제 참조 사진을 찾기 위한 용도. 엔드포인트/파라미터는 search_news와
        동일한 API HUB 계열이며, 공식 문서로 확인한 필드명을 그대로 쓴다.
        """
        normalized_query = " ".join((query or "").split())
        if not normalized_query:
            raise ValueError("이미지 검색어는 비어 있을 수 없습니다")
        if not 1 <= int(display) <= 100:
            raise ValueError("display는 1~100이어야 합니다")
        if not 1 <= int(start) <= 1000:
            raise ValueError("start는 1~1000이어야 합니다")
        if sort not in {"sim", "date"}:
            raise ValueError("sort는 sim 또는 date여야 합니다")
        if filter not in {"all", "large", "medium", "small"}:
            raise ValueError("filter는 all/large/medium/small 중 하나여야 합니다")
        response = httpx.get(
            self.IMAGE_ENDPOINT,
            params={
                "query": normalized_query,
                "display": int(display),
                "start": int(start),
                "sort": sort,
                "filter": filter,
                "format": "json",
            },
            headers=self._headers,
            timeout=12,
            follow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("NAVER 이미지 검색 API 응답 형식이 올바르지 않습니다")
        return body

    def search_trend(
        self,
        *,
        start_date: str,
        end_date: str,
        time_unit: str,
        keyword_groups: Iterable[dict[str, Any]],
        device: str | None = None,
        gender: str | None = None,
        ages: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """주제어 묶음의 통합검색 상대 추이를 반환한다.

        ratio는 절대 검색량이 아니라 요청 결과의 최댓값을 100으로 둔
        상대값이다. 호출자는 이를 금융 수치 또는 인과 근거로 해석하면 안 된다.
        """
        try:
            parsed_start = date.fromisoformat(start_date)
            parsed_end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("조회 날짜는 yyyy-mm-dd 형식이어야 합니다") from exc
        if parsed_start > parsed_end:
            raise ValueError("조회 시작일은 종료일보다 늦을 수 없습니다")
        if time_unit not in {"date", "week", "month"}:
            raise ValueError("timeUnit은 date, week, month 중 하나여야 합니다")
        groups = list(keyword_groups)
        if not 1 <= len(groups) <= 5:
            raise ValueError("keywordGroups는 1~5개여야 합니다")
        for group in groups:
            name = str(group.get("groupName") or "").strip()
            keywords = group.get("keywords")
            if not name or not isinstance(keywords, list) or not 1 <= len(keywords) <= 20:
                raise ValueError("각 keywordGroup에는 이름과 1~20개의 검색어가 필요합니다")
            if any(not str(keyword).strip() for keyword in keywords):
                raise ValueError("검색어는 비어 있을 수 없습니다")

        payload: dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
            "timeUnit": time_unit,
            "keywordGroups": groups,
        }
        if device is not None:
            if device not in {"pc", "mo"}:
                raise ValueError("device는 pc 또는 mo여야 합니다")
            payload["device"] = device
        if gender is not None:
            if gender not in {"m", "f"}:
                raise ValueError("gender는 m 또는 f여야 합니다")
            payload["gender"] = gender
        if ages is not None:
            normalized_ages = [str(age) for age in ages]
            if any(age not in {str(number) for number in range(1, 12)} for age in normalized_ages):
                raise ValueError("ages는 1~11 값만 사용할 수 있습니다")
            payload["ages"] = normalized_ages

        response = httpx.post(
            self.SEARCH_TREND_ENDPOINT,
            json=payload,
            headers={**self._headers, "Content-Type": "application/json"},
            timeout=12,
            follow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("NAVER 검색어 트렌드 API 응답 형식이 올바르지 않습니다")
        return body


def naver_api_hub_configured() -> bool:
    """기능 활성화와 인증 정보가 모두 준비됐는지 반환한다."""
    return bool(NAVER_API_HUB_ENABLED and NAVER_API_HUB_CLIENT_ID and NAVER_API_HUB_CLIENT_SECRET)
