"""Versioned campaign review with explicit, single-attempt publishing."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from dataclasses import asdict
from urllib.parse import urlparse

from nexus.adapters.social.base import SocialPost, derive_idempotency_key
from nexus.core.campaign import (
    ApprovalDecision,
    CampaignContext,
    CampaignState,
    CampaignStatus,
    ContentBundle,
    PlatformContent,
    PublishRecord,
)
from nexus.core.studio_catalog import _text

PLATFORMS = {"linkedin", "discord", "x", "meta_facebook", "meta_instagram"}


class StudioCampaigns:
    def __init__(self, workbench, factory=None):
        self.workbench = workbench
        self.studio = workbench.studio
        self.factory = factory or self.default_adapter
        # One service owns each state directory. Never repeat an interrupted publish.
        for record in [] if self.studio.sql_state else self.studio.records("campaigns", limit=None):
            if record.get("status") == "publishing":
                record.update(
                    status="uncertain",
                    recovery_note="Process stopped during publishing. Review the remote account; this attempt will not be retried.",
                )
            elif record.get("status") == "validating":
                record.update(
                    status="draft",
                    validation={
                        "revision": record["revision"],
                        "passed": False,
                        "error": "Validation interrupted. Validate this revision again.",
                    },
                )
            else:
                continue
            record.pop("active_run", None)
            self.write(record)

    def default_adapter(self, platform, identity):
        values = {}
        if self.studio.team:
            self.studio.team.assert_execution(identity)
            from nexus.core.auth.credential_crypto import decrypt_secret
            from nexus.core.auth.credential_store import get_user_credentials

            record = get_user_credentials(identity)

            def secret(name):
                encrypted = getattr(record, name, None)
                return decrypt_secret(encrypted) if encrypted else ""

            if platform == "linkedin":
                from nexus.connectors.linkedin import linkedin_connector_service

                connection = linkedin_connector_service.get_connection(nexus_id=identity)
                if connection.is_expired:
                    raise ValueError("LinkedIn account authorization expired")
                values = {"token": connection.access_token, "author": connection.author_urn}
            elif platform == "x":
                values = {
                    "token": secret("x_oauth_token_enc"),
                    "secret": secret("x_oauth_secret_enc"),
                }
            elif platform.startswith("meta_"):
                values = {
                    "token": secret("meta_page_token_enc"),
                    "page": getattr(record, "meta_page_id", None),
                    "instagram": getattr(record, "meta_ig_account_id", None),
                }
            else:
                raise ValueError("Configure a per-user Discord publisher through the embedding API")
        else:
            if platform == "linkedin":
                values = {
                    "token": os.getenv("NEXUS_LINKEDIN_ACCESS_TOKEN"),
                    "author": os.getenv("NEXUS_LINKEDIN_AUTHOR_URN"),
                }
            elif platform == "discord":
                values = {"webhook": os.getenv("NEXUS_DISCORD_SOCIAL_WEBHOOK_URL")}
            elif platform == "x":
                values = {
                    "token": os.getenv("NEXUS_X_OAUTH_TOKEN"),
                    "secret": os.getenv("NEXUS_X_OAUTH_SECRET"),
                }
            else:
                values = {
                    "token": os.getenv("NEXUS_META_PAGE_TOKEN"),
                    "page": os.getenv("NEXUS_META_PAGE_ID"),
                    "instagram": os.getenv("NEXUS_META_IG_ACCOUNT_ID"),
                }
        if not any(values.values()):
            raise ValueError("Publishing account is not configured")
        if platform == "linkedin":
            from nexus.adapters.social.linkedin_publisher import LinkedInSocialAdapter

            return LinkedInSocialAdapter(access_token=values["token"], author_urn=values["author"])
        if platform == "discord":
            from nexus.adapters.social.discord_publisher import DiscordSocialAdapter

            return DiscordSocialAdapter(webhook_url=values["webhook"])
        if platform == "x":
            from nexus.adapters.social.x_publisher import XSocialAdapter

            return XSocialAdapter(oauth_token=values["token"], oauth_token_secret=values["secret"])
        from nexus.adapters.social.meta_publisher import MetaSocialAdapter

        return MetaSocialAdapter(
            page_access_token=values["token"],
            page_id=values.get("page"),
            instagram_account_id=values.get("instagram"),
            target="instagram" if platform == "meta_instagram" else "facebook",
        )

    def record(self, identity, actor, own=False):
        return self.workbench.visible(
            self.studio._read(self.studio._path("campaigns", identity)), actor, own=own
        )

    def write(self, record):
        from nexus.core.studio_workbench import now

        record["updated_at"] = now()
        self.studio._write(self.studio._path("campaigns", record["id"]), record)

    def state(self, record):
        state = record["state"]
        context = CampaignContext(**state["campaign"])
        bundle = ContentBundle(
            campaign_id=record["id"],
            platforms=[PlatformContent(**item) for item in state["content_bundle"]["platforms"]],
        )
        return CampaignState(
            campaign=context,
            content_bundle=bundle,
            approval_decisions=[
                ApprovalDecision(**item) for item in state.get("approval_decisions", [])
            ],
            publish_results=[PublishRecord(**item) for item in state.get("publish_results", [])],
            status=CampaignStatus(state["status"]),
        )

    def save(self, payload, actor):
        name = _text(payload.get("name"), "Campaign name", 200)
        channels = payload.get("channels")
        if (
            not isinstance(channels, dict)
            or not 1 <= len(channels) <= 5
            or set(channels) - PLATFORMS
        ):
            raise ValueError("Choose 1–5 supported publishing channels")
        content = []
        for platform, item in channels.items():
            if not isinstance(item, dict):
                raise ValueError("Each channel needs content")
            text = _text(item.get("content"), "Post content", 65000)
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict) or set(metadata) - {
                "link_url",
                "link_title",
                "link_description",
                "thread_mode",
            }:
                raise ValueError("Unsupported publishing metadata")
            if any(not isinstance(value, (str, bool)) for value in metadata.values()):
                raise ValueError("Publishing metadata must contain text or booleans")
            if metadata.get("link_url"):
                url = urlparse(str(metadata["link_url"]))
                if url.scheme != "https" or not url.netloc or url.username or url.password:
                    raise ValueError("Link previews require an HTTPS URL without credentials")
            media = item.get("media_urls", [])
            if (
                not isinstance(media, list)
                or len(media) > 4
                or any(
                    not isinstance(url, str)
                    or len(url) > 2000
                    or urlparse(url).scheme != "https"
                    or not urlparse(url).netloc
                    or urlparse(url).username
                    or urlparse(url).password
                    for url in media
                )
            ):
                raise ValueError("Use at most four HTTPS media URLs without credentials")
            SocialPost(platform=platform, content=text, campaign_id="validation", metadata=metadata)
            content.append(
                PlatformContent(platform=platform, copy=text, media_refs=media, metadata=metadata)
            )
        with self.studio.lock:
            identity = payload.get("id") or uuid.uuid4().hex
            path = self.studio._path("campaigns", identity)
            previous = self.record(identity, actor, own=True) if path.exists() else None
            if payload.get("revision", 0) != (previous["revision"] if previous else 0):
                raise ValueError("Campaign changed. Reopen its latest revision")
            if previous and previous["status"] in {
                "validating",
                "publishing",
                "published",
                "uncertain",
            }:
                raise ValueError(
                    "This campaign is in flight or has a publish attempt. Create a new campaign to revise it"
                )
            state = CampaignState(
                campaign=CampaignContext(
                    campaign_id=identity,
                    objective=name,
                    audience=str(payload.get("audience") or "")[:1000],
                    channels=list(channels),
                ),
                content_bundle=ContentBundle(campaign_id=identity, platforms=content),
            )
            record = {
                "id": identity,
                "name": name,
                "owner_id": self.workbench.owner(actor),
                "revision": (previous["revision"] if previous else 0) + 1,
                "status": "draft",
                "state": state.to_dict(),
            }
            self.write(record)
            return record

    def approve(self, identity, payload, actor, approved):
        with self.studio.lock:
            record = self.record(identity, actor)
            if payload.get("revision") != record["revision"] or record["status"] not in {
                "draft",
                "approved",
                "rejected",
            }:
                raise ValueError("Campaign changed or cannot be reviewed in its current state")
            if approved and record.get("validation", {}).get("revision") != record["revision"]:
                raise ValueError("Validate this exact revision before approving it")
            if approved and not record.get("validation", {}).get("passed"):
                raise ValueError("Resolve content or account validation errors before approving")
            state = self.state(record)
            state.add_approval(
                ApprovalDecision(
                    approved=approved,
                    reviewer=str(self.workbench.owner(actor) or "desktop"),
                    notes=str(payload.get("notes") or "")[:2000],
                )
            )
            record.update(state=state.to_dict(), status="approved" if approved else "rejected")
            self.write(record)
            return record

    def prepare(self, payload):
        actor = self.workbench.actor(payload)
        record = self.record(payload.get("campaign_id"), actor, own=True)
        if payload.get("revision") != record["revision"]:
            raise ValueError("Campaign revision changed")
        action = payload.get("campaign_action")
        if action not in {"validate", "publish"}:
            raise ValueError("Choose validate or publish")
        if action == "publish" and (
            payload.get("confirm_publish") is not True or record["status"] != "approved"
        ):
            raise ValueError(
                "Review and approve the current revision, then explicitly confirm publishing"
            )
        payload["task"] = ("Publish " if action == "publish" else "Validate ") + record["name"]

    async def execute(self, run_id, payload):
        actor = self.workbench.actor(payload)
        identity = payload["campaign_id"]
        action = payload["campaign_action"]
        with self.studio.lock:
            self.prepare(payload)
            record = self.record(identity, actor, own=True)
            if record["status"] in {"validating", "publishing", "published", "uncertain"}:
                raise ValueError("This campaign already has an active or completed publish attempt")
            state = self.state(record)
            record.update(
                status="validating" if action == "validate" else "publishing", active_run=run_id
            )
            self.write(record)
        outcomes = []
        finished = False
        try:
            adapters = {}
            for item in state.content_bundle.platforms:
                try:
                    adapter = self.factory(item.platform, self.workbench.owner(actor))
                    adapters[item.platform] = (
                        await adapter if inspect.isawaitable(adapter) else adapter
                    )
                except Exception:
                    raise RuntimeError(
                        "Publishing account setup failed. Check your configured account and credentials"
                    ) from None
            for item in state.content_bundle.platforms:
                if self.studio.team:
                    self.studio.team.assert_execution(self.workbench.owner(actor))
                post = SocialPost(
                    platform=item.platform,
                    content=item.copy,
                    campaign_id=identity,
                    media_urls=item.media_refs,
                    metadata=item.metadata,
                )
                self.studio._update_run(run_id, step=item.platform)
                adapter = adapters[item.platform]
                try:
                    result = await asyncio.wait_for(
                        adapter.dry_run(post) if action == "validate" else adapter.publish(post),
                        timeout=60,
                    )
                except Exception:
                    raise RuntimeError(
                        "Publishing provider did not return a confirmed result. Review the recorded campaign state and remote account"
                    ) from None
                outcome = PublishRecord(
                    platform=item.platform,
                    success=bool(result.success),
                    post_id=result.post_id,
                    idempotency_key=derive_idempotency_key(
                        identity, item.platform, str(record["revision"])
                    ),
                    dry_run=action == "validate",
                    error=None
                    if result.success
                    else "Provider rejected this attempt; inspect the account before retrying",
                    published_at=result.published_at,
                )
                outcomes.append(asdict(outcome))
                if action == "publish":
                    state.add_publish_result(outcome)
                    record["state"] = state.to_dict()
                    self.write(record)  # persist each outcome before attempting another channel
                if not result.success:
                    break
            passed = len(outcomes) == len(state.content_bundle.platforms) and all(
                item["success"] for item in outcomes
            )
            if action == "validate":
                record.update(
                    status="draft",
                    validation={
                        "revision": record["revision"],
                        "passed": passed,
                        "outcomes": outcomes,
                    },
                )
            else:
                state.status = CampaignStatus.PUBLISHED if passed else CampaignStatus.FAILED
                record.update(status="published" if passed else "uncertain", state=state.to_dict())
            self.write(record)
            finished = True
            if not passed:
                raise RuntimeError("Campaign checks or publishing failed; review recorded outcomes")
            return json.dumps(
                {"campaign_id": identity, "action": action, "outcomes": outcomes}, indent=2
            )
        finally:
            with self.studio.lock:
                if not finished:
                    if action == "validate":
                        record.update(
                            status="draft",
                            validation={
                                "revision": record["revision"],
                                "passed": False,
                                "error": "Account setup or content validation failed",
                            },
                        )
                    else:
                        record["status"] = "uncertain"
                record.pop("active_run", None)
                self.write(record)

    def post(self, parts, payload, actor):
        if parts == ["save"]:
            return 201, self.save(payload, actor)
        if len(parts) == 2 and parts[1] in {"approve", "reject"}:
            return 200, self.approve(parts[0], payload, actor, parts[1] == "approve")
        if len(parts) == 2 and parts[1] in {"validate", "publish"}:
            self.record(parts[0], actor, own=True)
            return 202, self.studio.start_run(
                {
                    **payload,
                    "kind": "campaign",
                    "campaign_id": parts[0],
                    "campaign_action": parts[1],
                }
            )
        raise FileNotFoundError("Unknown campaign action")
