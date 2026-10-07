from __future__ import annotations

from typing import List
from pydantic import BaseModel


class FeedSource(BaseModel):
    name: str
    url: str
    language: str
    default_category: str = "General"


DEFAULT_FEED_SOURCES: List[FeedSource] = [
    FeedSource(name="Українська правда", url="https://www.pravda.com.ua/rss/", language="uk", default_category="Politics"),
    FeedSource(name="Економічна правда", url="https://www.epravda.com.ua/rss/", language="uk", default_category="Economy"),
    FeedSource(name="NV (Новое Время)", url="https://nv.ua/ukr/rss/all.xml", language="uk", default_category="News"),
    FeedSource(name="ТСН", url="https://tsn.ua/rss/full.xml", language="uk", default_category="News"),
    FeedSource(name="Дзеркало тижня", url="https://zn.ua/rss/full.xml", language="uk", default_category="Politics"),
    FeedSource(name="BBC News Україна", url="https://feeds.bbci.co.uk/ukrainian/rss.xml", language="uk", default_category="World"),
    FeedSource(name="Радіо Свобода", url="https://www.radiosvoboda.org/api/zrqiteu_i_", language="uk", default_category="Politics"),
    FeedSource(name="Інтерфакс-Україна", url="https://interfax.com.ua/news/general.rss", language="uk"),
    FeedSource(name="BBC News World", url="http://feeds.bbci.co.uk/news/world/rss.xml", language="en", default_category="World"),
    FeedSource(name="BBC News Politics", url="http://feeds.bbci.co.uk/news/politics/rss.xml", language="en", default_category="Politics"),
    FeedSource(name="BBC News Business", url="http://feeds.bbci.co.uk/news/business/rss.xml", language="en", default_category="Business"),
    FeedSource(name="BBC News Technology", url="http://feeds.bbci.co.uk/news/technology/rss.xml", language="en", default_category="Technology"),
    FeedSource(name="The Guardian World", url="https://www.theguardian.com/world/rss", language="en", default_category="World"),
    FeedSource(name="The Guardian Politics", url="https://www.theguardian.com/politics/rss", language="en", default_category="Politics"),
    FeedSource(name="DW English", url="https://rss.dw.com/rdf/rss-en-all", language="en", default_category="World"),
    FeedSource(name="NPR News", url="https://feeds.npr.org/1001/rss.xml", language="en", default_category="News"),
    FeedSource(name="Der Spiegel", url="https://www.spiegel.de/schlagzeilen/index.rss", language="de", default_category="News"),
    FeedSource(name="Tagesschau", url="https://www.tagesschau.de/infoservices/alle-meldungen-100~rss2.xml", language="de", default_category="News"),
    FeedSource(name="DW Deutsch", url="https://rss.dw.com/rdf/rss-de-all", language="de", default_category="World"),
    FeedSource(name="Die Zeit", url="https://newsfeed.zeit.de/index", language="de"),
]
