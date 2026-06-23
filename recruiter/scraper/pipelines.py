"""Scrapy item pipeline: write scraped candidates into the shared SQLite store."""
from __future__ import annotations

from .. import db


class SQLitePipeline:
    def open_spider(self, spider):
        db.init_db()

    def process_item(self, item, spider):
        data = dict(item)
        if not data.get("name"):
            spider.logger.info("Skipping item with no name: %s", data.get("profile_url"))
            return item
        with db.connect() as conn:
            db.upsert_candidate(conn, data)
        return item
