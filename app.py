#!/usr/bin/env python3
"""
Streamlit UI for browsing and curating CoreX Sales data in corex_leads.db.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

import db as dbmod

DB_PATH = str(Path(__file__).resolve().parent / "corex_leads.db")


def _cache_rev() -> int:
    return int(st.session_state.get("_db_rev", 0))


def _bump_cache_rev() -> None:
    st.session_state["_db_rev"] = _cache_rev() + 1


@st.cache_data(ttl=60)
def _load_leads(db_path: str) -> List[Dict[str, Any]]:
    conn = dbmod.get_connection(db_path)
    try:
        return dbmod.get_all_leads(conn)
    finally:
        conn.close()


@st.cache_data(ttl=30)
def _load_qualified(db_path: str, _rev: int) -> List[Dict[str, Any]]:
    conn = dbmod.get_connection(db_path)
    try:
        return dbmod.get_all_qualified_leads(conn)
    finally:
        conn.close()


@st.cache_data(ttl=60)
def _load_tracker(db_path: str, _rev: int) -> List[Dict[str, Any]]:
    conn = dbmod.get_connection(db_path)
    try:
        return dbmod.get_all_tracker_rows(conn)
    finally:
        conn.close()


@st.cache_data(ttl=30)
def _load_tracker_export(
    db_path: str,
    approved_only: bool,
    _rev: int,
) -> List[Dict[str, Any]]:
    conn = dbmod.get_connection(db_path)
    try:
        if approved_only:
            return dbmod.get_tracker_rows_approved_qualified(conn)
        return dbmod.get_all_tracker_rows(conn)
    finally:
        conn.close()


@st.cache_data(ttl=60)
def _load_exports(db_path: str) -> List[Dict[str, Any]]:
    conn = dbmod.get_connection(db_path)
    try:
        return dbmod.get_recent_exports(conn, 5)
    finally:
        conn.close()


def _norm_review_status(row: Dict[str, Any]) -> str:
    s = str(row.get("review_status") or "").strip().lower()
    if s in dbmod.REVIEW_STATUS_VALUES:
        return s
    return dbmod.REVIEW_STATUS_PENDING


def _fit_tier_letter(fit_tier: Any) -> str:
    s = str(fit_tier or "").lower()
    if "tier 1" in s:
        return "A"
    if "tier 2" in s:
        return "B"
    if "tier 3" in s:
        return "C"
    return "D"


def _province_value(row: Dict[str, Any]) -> str:
    return str(row.get("province_normalized") or row.get("province") or "").strip()


def _browse_haystack(row: Dict[str, Any]) -> str:
    keys = (
        "upload_signals",
        "types",
        "editorial_summary",
        "primary_type",
        "business_name",
        "notes",
    )
    return " ".join(str(row.get(k) or "") for k in keys).lower()


def _service_tags(row: Dict[str, Any]) -> List[str]:
    h = _browse_haystack(row)
    tags: List[str] = []
    if "giclee" in h:
        tags.append("Giclée")
    if "canvas" in h:
        tags.append("Canvas")
    if "acrylic" in h or "plexi" in h:
        tags.append("Acrylic")
    if "fram" in h or "matting" in h:
        tags.append("Framing")
    if "large format" in h or "wide format" in h or "banner" in h:
        tags.append("Large format")
    return tags


def _platform_bucket(platform: Any) -> str:
    p = str(platform or "").strip().lower()
    if p in ("", "unknown"):
        return "Other"
    if "wordpress" in p:
        return "WordPress"
    if "shopify" in p:
        return "Shopify"
    return "Other"


def _df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def _href_url(raw: str) -> str:
    u = (raw or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        return "https://" + u
    return u


def _lead_has_no_website(r: Dict[str, Any]) -> bool:
    return not (
        str(r.get("website") or "").strip() or str(r.get("normalized_url") or "").strip()
    )


def _lead_fit_score_zero(r: Dict[str, Any]) -> bool:
    raw = str(r.get("fit_score") or "").strip()
    if raw == "":
        return False
    try:
        return int(raw) == 0
    except ValueError:
        return False


def _lead_not_wordpress(r: Dict[str, Any]) -> bool:
    return _platform_bucket(r.get("platform")) != "WordPress"


def _lead_fit_tier_d_or_tier3(r: Dict[str, Any]) -> bool:
    ft = str(r.get("fit_tier") or "")
    if "tier 3" in ft.lower():
        return True
    return _fit_tier_letter(ft) == "D"


def _persist_review_status_and_notes(
    conn: sqlite3.Connection,
    lead_id: int,
    status: str,
    notes: str,
) -> None:
    sk = (status or "").strip().lower()
    if sk not in dbmod.REVIEW_STATUS_VALUES:
        raise ValueError(f"invalid review status: {status!r}")
    conn.execute(
        "UPDATE qualified_leads SET review_status = ?, notes = ? WHERE id = ?",
        (sk, notes or "", int(lead_id)),
    )
    conn.commit()


def _review_city_line(r: Dict[str, Any]) -> str:
    city = str(r.get("city") or "").strip()
    prov = _province_value(r)
    if city and prov:
        return f"{city}, {prov}"
    return city or prov or "—"


def _upload_cta_label(r: Dict[str, Any]) -> str:
    v = str(r.get("upload_present") or "").strip().lower()
    if v == "yes":
        return "Detected"
    if v == "no":
        return "Not detected"
    return str(r.get("upload_present") or "").strip() or "—"


def _woo_label(r: Dict[str, Any]) -> str:
    v = str(r.get("woocommerce_detected") or "").strip().lower()
    if v == "yes":
        return "Yes"
    if v == "no":
        return "No"
    return str(r.get("woocommerce_detected") or "").strip() or "—"


def _review_tier_filter_matches(tier_ui: str, letter: str) -> bool:
    if tier_ui == "All":
        return True
    if tier_ui == "Tier A+B":
        return letter in ("A", "B")
    if tier_ui == "Tier A":
        return letter == "A"
    if tier_ui == "Tier B":
        return letter == "B"
    return True


def _next_list_selection_id(ordered_ids: List[int], current_id: int) -> Optional[int]:
    if not ordered_ids:
        return None
    try:
        i = ordered_ids.index(current_id)
    except ValueError:
        return ordered_ids[0]
    if i + 1 < len(ordered_ids):
        return ordered_ids[i + 1]
    if i > 0:
        return ordered_ids[i - 1]
    return None


def _tier_badge_html(letter: str) -> str:
    styles = {
        "A": ("#EAF3DE", "#27500A"),
        "B": ("#E6F1FB", "#0C447C"),
        "C": ("#FAEEDA", "#633806"),
        "D": ("#FCEBEB", "#791F1F"),
    }
    bg, fg = styles.get(letter, ("#f0f0f0", "#333333"))
    return (
        f'<span style="background:{bg};color:{fg};padding:4px 10px;'
        f'border-radius:20px;font-size:12px;font-weight:600;">Tier {letter}</span>'
    )


def main() -> None:
    st.set_page_config(page_title="CoreX Sales", layout="wide")
    st.title("CoreX Sales")
    st.session_state.setdefault("_db_rev", 0)
    rev = _cache_rev()

    if not Path(DB_PATH).exists():
        st.warning(
            f"No database file at `{DB_PATH}`. Run the pipeline or place "
            "`corex_leads.db` next to `app.py`."
        )

    leads = _load_leads(DB_PATH) if Path(DB_PATH).exists() else []
    qualified = _load_qualified(DB_PATH, rev) if Path(DB_PATH).exists() else []
    tracker_rows = _load_tracker(DB_PATH, rev) if Path(DB_PATH).exists() else []
    export_log = _load_exports(DB_PATH) if Path(DB_PATH).exists() else []

    tab_find, tab_qualify, tab_review, tab_browse, tab_export = st.tabs(
        ["Find", "Qualify", "Review", "Browse", "Export"]
    )

    with tab_find:
        st.header("Lead Finder")
        st.metric("Leads in database", len(leads))
        if not leads:
            st.info("No data yet")
        else:
            provinces = sorted(
                {p for r in leads if (p := _province_value(r))},
                key=lambda x: x.lower(),
            )
            c1, c2 = st.columns(2)
            with c1:
                prov_sel = st.selectbox(
                    "Province",
                    options=["All"] + provinces,
                    key="find_prov",
                )
            with c2:
                q_find = st.text_input(
                    "Search business name",
                    "",
                    key="find_q",
                ).strip().lower()

            rows_f = []
            for r in leads:
                pv = _province_value(r)
                if prov_sel != "All" and pv != prov_sel:
                    continue
                name = str(r.get("business_name") or "")
                if q_find and q_find not in name.lower():
                    continue
                rows_f.append(
                    {
                        "business_name": r.get("business_name"),
                        "city": r.get("city"),
                        "province": r.get("province"),
                        "website": r.get("website"),
                        "formatted_phone_number": r.get("formatted_phone_number"),
                        "rating": r.get("rating"),
                        "primary_type": r.get("primary_type"),
                    }
                )
            df_f = pd.DataFrame(rows_f)
            st.dataframe(df_f, use_container_width=True, hide_index=True)
            st.download_button(
                label="Download filtered leads as CSV",
                data=_df_to_csv_bytes(df_f),
                file_name="leads_filtered.csv",
                mime="text/csv",
                disabled=df_f.empty,
            )

    with tab_qualify:
        st.header("Lead Qualifier")
        st.metric("Qualified leads in database", len(qualified))
        if not qualified:
            st.info("No data yet")
        else:
            prov_q = sorted(
                {p for r in qualified if (p := _province_value(r))},
                key=lambda x: x.lower(),
            )
            c1, c2, c3 = st.columns(3)
            with c1:
                tier_sel = st.selectbox(
                    "Fit tier",
                    options=["All", "A", "B", "C", "D"],
                    key="qual_tier",
                )
            with c2:
                prov_sel_q = st.selectbox(
                    "Province",
                    options=["All"] + prov_q,
                    key="qual_prov",
                )
            with c3:
                q_qual = st.text_input(
                    "Search business name",
                    "",
                    key="qual_q",
                ).strip().lower()

            rows_q: List[Dict[str, Any]] = []
            for r in qualified:
                letter = _fit_tier_letter(r.get("fit_tier"))
                if tier_sel != "All" and letter != tier_sel:
                    continue
                pv = _province_value(r)
                if prov_sel_q != "All" and pv != prov_sel_q:
                    continue
                name = str(r.get("business_name") or "")
                if q_qual and q_qual not in name.lower():
                    continue
                rows_q.append(
                    {
                        "business_name": r.get("business_name"),
                        "city": r.get("city"),
                        "province": r.get("province"),
                        "website": r.get("website"),
                        "fit_tier": r.get("fit_tier"),
                        "fit_score": r.get("fit_score"),
                        "platform": r.get("platform"),
                        "has_woocommerce": r.get("woocommerce_detected"),
                        "has_artwork_upload_cta": r.get("upload_present"),
                        "contact_email": r.get("email_found"),
                    }
                )
            df_q = pd.DataFrame(rows_q)

            def _style_fit_tier(s: pd.Series) -> List[str]:
                styles = []
                for v in s:
                    letter = _fit_tier_letter(v)
                    color = {
                        "A": "#d4edda",
                        "B": "#cfe2ff",
                        "C": "#fff3cd",
                        "D": "#f8d7da",
                    }.get(letter, "#e9ecef")
                    styles.append(f"background-color: {color}")
                return styles

            if not df_q.empty and "fit_tier" in df_q.columns:
                styler = df_q.style.apply(
                    lambda col: _style_fit_tier(col)
                    if col.name == "fit_tier"
                    else [""] * len(col),
                    axis=0,
                )
                st.dataframe(styler, use_container_width=True, hide_index=True)
            else:
                st.dataframe(df_q, use_container_width=True, hide_index=True)

            st.download_button(
                label="Download filtered results as CSV",
                data=_df_to_csv_bytes(df_q),
                file_name="qualified_filtered.csv",
                mime="text/csv",
                disabled=df_q.empty,
            )

    with tab_review:
        st.header("Lead Review")
        if not qualified:
            st.info("No data yet")
        else:
            n_pend = sum(
                1 for r in qualified if _norm_review_status(r) == dbmod.REVIEW_STATUS_PENDING
            )
            n_appr = sum(
                1 for r in qualified if _norm_review_status(r) == dbmod.REVIEW_STATUS_APPROVED
            )
            n_rej = sum(
                1 for r in qualified if _norm_review_status(r) == dbmod.REVIEW_STATUS_REJECTED
            )
            m1, m2, m3 = st.columns(3)
            m1.metric("Pending", n_pend)
            m2.metric("Approved", n_appr)
            m3.metric("Rejected", n_rej)

            y_total = len(qualified)
            x_reviewed = n_appr + n_rej
            pct = int(round(100.0 * x_reviewed / y_total)) if y_total else 0
            st.progress(min(1.0, x_reviewed / y_total) if y_total else 0.0)
            st.caption(f"{x_reviewed} of {y_total} leads reviewed ({pct}%)")

            with st.expander("Smart bulk actions", expanded=False):
                st.caption(
                    "Presets scan **all** qualified leads in the database (ignores filters below)."
                )

                def _run_smart_reject(ids: List[int]) -> None:
                    if not ids:
                        return
                    conn = dbmod.get_connection(DB_PATH)
                    try:
                        dbmod.bulk_set_review_status(
                            conn, ids, dbmod.REVIEW_STATUS_REJECTED
                        )
                    finally:
                        conn.close()
                    _bump_cache_rev()
                    st.rerun()

                ids_no_web = [
                    int(r["id"]) for r in qualified if _lead_has_no_website(r)
                ]
                st.write(f"**No website detected** — {len(ids_no_web)} leads")
                if st.button(
                    f"Reject all {len(ids_no_web)} leads",
                    key="smart_reject_no_web",
                    disabled=len(ids_no_web) == 0,
                ):
                    _run_smart_reject(ids_no_web)

                ids_fs0 = [int(r["id"]) for r in qualified if _lead_fit_score_zero(r)]
                st.write(f"**Fit score 0** — {len(ids_fs0)} leads")
                if st.button(
                    f"Reject all {len(ids_fs0)} leads",
                    key="smart_reject_fs0",
                    disabled=len(ids_fs0) == 0,
                ):
                    _run_smart_reject(ids_fs0)

                ids_nwp = [int(r["id"]) for r in qualified if _lead_not_wordpress(r)]
                st.write(f"**Not WordPress** — {len(ids_nwp)} leads")
                if st.button(
                    f"Reject all {len(ids_nwp)} leads",
                    key="smart_reject_nwp",
                    disabled=len(ids_nwp) == 0,
                ):
                    _run_smart_reject(ids_nwp)

                ids_d = [
                    int(r["id"]) for r in qualified if _lead_fit_tier_d_or_tier3(r)
                ]
                st.write(
                    f"**Fit tier D or Tier 3** — {len(ids_d)} leads "
                    '(letter D or "Tier 3" in fit tier text)'
                )
                if st.button(
                    f"Reject all {len(ids_d)} leads",
                    key="smart_reject_d",
                    disabled=len(ids_d) == 0,
                ):
                    _run_smart_reject(ids_d)

            rev_filter_map = {
                "Pending": dbmod.REVIEW_STATUS_PENDING,
                "Approved": dbmod.REVIEW_STATUS_APPROVED,
                "Rejected": dbmod.REVIEW_STATUS_REJECTED,
            }

            col_left, col_right = st.columns([35, 65])
            with col_left:
                tier_ui = st.selectbox(
                    "Tier",
                    options=["Tier A+B", "Tier A", "Tier B", "All"],
                    index=0,
                    key="rev_tier_ui",
                )
                rev_stat_ui = st.selectbox(
                    "Review status",
                    options=["Pending", "Approved", "Rejected", "All"],
                    index=0,
                    key="rev_status",
                )
                q_rev = st.text_input(
                    "Search",
                    "",
                    key="rev_q",
                    placeholder="Business name…",
                ).strip().lower()

            filtered_rev: List[Dict[str, Any]] = []
            for r in qualified:
                if rev_stat_ui != "All":
                    if _norm_review_status(r) != rev_filter_map[rev_stat_ui]:
                        continue
                letter = _fit_tier_letter(r.get("fit_tier"))
                if not _review_tier_filter_matches(tier_ui, letter):
                    continue
                name = str(r.get("business_name") or "")
                city_l = str(r.get("city") or "").lower()
                if q_rev and q_rev not in name.lower() and q_rev not in city_l:
                    continue
                filtered_rev.append(r)

            fid_list = [int(r["id"]) for r in filtered_rev]
            st.session_state.setdefault("rev_selected_id", None)
            sel_id_raw = st.session_state.get("rev_selected_id")
            sel_id: Optional[int] = (
                int(sel_id_raw) if sel_id_raw is not None else None
            )
            if fid_list:
                if sel_id is None or sel_id not in fid_list:
                    st.session_state["rev_selected_id"] = fid_list[0]
                    sel_id = fid_list[0]
            else:
                st.session_state["rev_selected_id"] = None
                sel_id = None

            selected_row: Optional[Dict[str, Any]] = None
            if sel_id is not None:
                selected_row = next(
                    (r for r in filtered_rev if int(r["id"]) == sel_id), None
                )

            with col_left:
                st.caption(f"{len(filtered_rev)} lead(s) in this view")
                for r in filtered_rev:
                    rid = int(r["id"])
                    selected = rid == sel_id
                    biz = str(r.get("business_name") or "—")
                    letter = _fit_tier_letter(r.get("fit_tier"))
                    plat = _platform_bucket(r.get("platform")) or str(
                        r.get("platform") or "—"
                    )
                    city = str(r.get("city") or "—")
                    line2 = f"{city} · Tier {letter} · {plat}"
                    label = f"{biz}\n{line2}"
                    if st.button(
                        label,
                        key=f"rev_pick_{rid}",
                        use_container_width=True,
                        type="primary" if selected else "secondary",
                    ):
                        st.session_state["rev_selected_id"] = rid
                        st.rerun()

            with col_right:
                if not selected_row:
                    st.info("Select a lead from the list to review.")
                else:
                    rid = int(selected_row["id"])
                    biz = str(selected_row.get("business_name") or "—")
                    letter = _fit_tier_letter(selected_row.get("fit_tier"))
                    web = str(
                        selected_row.get("website")
                        or selected_row.get("normalized_url")
                        or ""
                    ).strip()
                    href = _href_url(web)
                    top_l, top_r = st.columns([4, 1])
                    with top_l:
                        st.markdown(f"### {biz}")
                        if web and href:
                            st.markdown(
                                f'<a href="{href}" target="_blank" rel="noopener noreferrer" '
                                f'style="color:#0068c9;text-decoration:none;">{web}</a>',
                                unsafe_allow_html=True,
                            )
                        elif web:
                            st.markdown(
                                f'<span style="color:#0068c9;">{web}</span>',
                                unsafe_allow_html=True,
                            )
                        else:
                            st.caption("No website")
                    with top_r:
                        st.markdown(_tier_badge_html(letter), unsafe_allow_html=True)

                    st.divider()

                    g1, g2 = st.columns(2)
                    with g1:
                        st.caption("City")
                        st.write(_review_city_line(selected_row))
                        st.caption("WooCommerce")
                        st.write(_woo_label(selected_row))
                        st.caption("Email")
                        st.write(str(selected_row.get("email_found") or "—"))
                    with g2:
                        st.caption("Platform")
                        st.write(
                            _platform_bucket(selected_row.get("platform"))
                            or str(selected_row.get("platform") or "—")
                        )
                        st.caption("Upload CTA")
                        st.write(_upload_cta_label(selected_row))
                        phone = str(
                            selected_row.get("formatted_phone_number")
                            or selected_row.get("international_phone_number")
                            or ""
                        ).strip()
                        st.caption("Phone")
                        st.write(phone or "—")

                    st.divider()

                    nk = f"rev_notes_{rid}"
                    if nk not in st.session_state:
                        st.session_state[nk] = str(selected_row.get("notes") or "")
                    st.text_area(
                        "Notes",
                        placeholder="Add a note...",
                        key=nk,
                        height=120,
                        label_visibility="collapsed",
                    )

                    st.divider()

                    def _after_review(new_status: str) -> None:
                        note_text = str(st.session_state.get(nk, ""))
                        nxt = _next_list_selection_id(fid_list, rid)
                        conn = dbmod.get_connection(DB_PATH)
                        try:
                            _persist_review_status_and_notes(
                                conn, rid, new_status, note_text
                            )
                        finally:
                            conn.close()
                        if nk in st.session_state:
                            del st.session_state[nk]
                        st.session_state["rev_selected_id"] = nxt
                        _bump_cache_rev()
                        st.rerun()

                    rb1, rb2 = st.columns(2)
                    with rb1:
                        if st.button(
                            "Reject",
                            key=f"rev_reject_{rid}",
                            use_container_width=True,
                            type="secondary",
                        ):
                            _after_review(dbmod.REVIEW_STATUS_REJECTED)
                    with rb2:
                        if st.button(
                            "Approve",
                            key=f"rev_approve_{rid}",
                            use_container_width=True,
                            type="primary",
                        ):
                            _after_review(dbmod.REVIEW_STATUS_APPROVED)

    with tab_browse:
        st.header("Lead Browser")
        if not qualified:
            st.info("No data yet")
        else:
            prov_b = sorted(
                {p for r in qualified if (p := _province_value(r))},
                key=lambda x: x.lower(),
            )
            filt_col, main_col = st.columns((1, 3))
            with filt_col:
                st.subheader("Filters")
                approved_only_browse = st.toggle(
                    "Approved only",
                    value=False,
                    key="browse_approved_only",
                )
                tier_b = st.selectbox(
                    "Fit tier",
                    ["All", "A", "B", "C", "D"],
                    key="browse_tier",
                )
                prov_b_sel = st.selectbox(
                    "Province",
                    ["All"] + prov_b,
                    key="browse_prov",
                )
                email_f = st.selectbox(
                    "Has email",
                    ["all", "yes", "no"],
                    key="browse_email",
                )
                upload_f = st.selectbox(
                    "Has upload CTA",
                    ["all", "yes", "no"],
                    key="browse_upload",
                )
                plat_f = st.selectbox(
                    "Platform",
                    ["All", "WordPress", "Shopify", "Other"],
                    key="browse_plat",
                )

            filtered: List[Dict[str, Any]] = []
            for r in qualified:
                if approved_only_browse and _norm_review_status(r) != dbmod.REVIEW_STATUS_APPROVED:
                    continue
                letter = _fit_tier_letter(r.get("fit_tier"))
                if tier_b != "All" and letter != tier_b:
                    continue
                pv = _province_value(r)
                if prov_b_sel != "All" and pv != prov_b_sel:
                    continue
                em = str(r.get("email_found") or "").strip()
                if email_f == "yes" and not em:
                    continue
                if email_f == "no" and em:
                    continue
                up = str(r.get("upload_present") or "").lower()
                if upload_f == "yes" and up != "yes":
                    continue
                if upload_f == "no" and up == "yes":
                    continue
                bucket = _platform_bucket(r.get("platform"))
                if plat_f == "WordPress" and bucket != "WordPress":
                    continue
                if plat_f == "Shopify" and bucket != "Shopify":
                    continue
                if plat_f == "Other" and bucket != "Other":
                    continue
                filtered.append(r)

            n = len(filtered)
            page_size = 20
            total_pages = max(1, (n + page_size - 1) // page_size)
            with main_col:
                page = st.number_input(
                    "Page",
                    min_value=1,
                    max_value=total_pages,
                    value=1,
                    step=1,
                    key="browse_page_num",
                )
                start = (page - 1) * page_size
                chunk = filtered[start : start + page_size]

                st.caption(
                    f"Showing {len(chunk)} of {n} leads (page {page} of {total_pages})"
                )

                for r in chunk:
                    letter = _fit_tier_letter(r.get("fit_tier"))
                    tags = _service_tags(r)
                    phone = str(
                        r.get("formatted_phone_number")
                        or r.get("international_phone_number")
                        or ""
                    ).strip()
                    web = str(r.get("website") or r.get("normalized_url") or "").strip()
                    href = _href_url(web)
                    with st.container():
                        st.markdown(f"### {r.get('business_name') or '—'}")
                        if web and href:
                            st.markdown(f"[{web}]({href})")
                        st.write(
                            f"**Fit tier** {letter} — {r.get('fit_tier') or '—'}  \n"
                            f"**Review:** {_norm_review_status(r)}"
                        )
                        st.write(
                            f"**Location:** {r.get('city') or '—'}, "
                            f"{_province_value(r) or '—'}"
                        )
                        st.write(
                            f"**Platform:** {r.get('platform') or '—'}  \n"
                            f"**WooCommerce:** {r.get('woocommerce_detected') or '—'}  \n"
                            f"**Upload CTA:** {r.get('upload_present') or '—'}"
                        )
                        em = str(r.get("email_found") or "").strip()
                        if em:
                            st.write(f"**Email:** {em}")
                        if phone:
                            st.write(f"**Phone:** {phone}")
                        if tags:
                            st.caption(
                                "Services (from signals): " + " · ".join(tags)
                            )
                        st.divider()

    with tab_export:
        st.header("Export to Sheets")
        export_appr_only = st.checkbox(
            "Export approved only",
            value=False,
            key="export_approved_only",
        )
        tracker_export = (
            _load_tracker_export(DB_PATH, export_appr_only, rev)
            if Path(DB_PATH).exists()
            else []
        )
        n_qualified_appr = sum(
            1 for r in qualified if _norm_review_status(r) == dbmod.REVIEW_STATUS_APPROVED
        )
        n_track_total = len(tracker_rows)
        n_track_export = len(tracker_export)
        st.metric("Tracker rows in database", n_track_total)
        st.caption(
            f"{n_qualified_appr} of {len(qualified)} qualified leads are approved. "
            f"Export will include {n_track_export} of {n_track_total} tracker rows "
            f"({'approved-qualified join only' if export_appr_only else 'all rows'})."
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in tracker_export:
            writer.writerow(dbmod.tracker_row_csv_values(row))
        tracker_csv = buf.getvalue().encode("utf-8")
        st.download_button(
            label="Download tracker CSV",
            data=tracker_csv,
            file_name="tracker_export.csv",
            mime="text/csv",
            disabled=len(tracker_export) == 0,
        )
        st.subheader("Recent exports")
        if not export_log:
            st.caption("No export log entries yet.")
        else:
            st.dataframe(
                pd.DataFrame(export_log),
                use_container_width=True,
                hide_index=True,
            )


if __name__ == "__main__":
    main()
