#!/usr/bin/env python3
import os
import json
import backoff
import requests
import singer
import datetime
from dateutil.parser import parse as parse_datetime
from singer import utils, metadata
from singer.catalog import Catalog, CatalogEntry
from singer.schema import Schema
from singer.transform import transform

REQUIRED_CONFIG_KEYS = ["start_date", "api_key", "domain"]
LOGGER = singer.get_logger()
HOST = "https://{domain}.freshcaller.com/api/v1"
END_POINTS = {
    "teams": "/teams",
    "users": "/users",
    "calls": "/calls",
    "call_metrics": "/call_metrics",
}

PAGE_RECORDS_LIMIT = 1000
INCREMENTAL_SYNC_STREAMS = ["calls", "call_metrics"]


class FreshcallerRateLimitError(Exception):
    def __init__(self, msg):
        self.msg = msg
        super().__init__(self.msg)


def get_key_properties(stream_id):
    # ???
    return ["id"]


def get_bookmark(stream_id):
    """
    Bookmarks for the streams which has incremental sync.
    """
    bookmarks = {
        "call_metrics": "created_time",
        "calls": "created_time"
    }
    return bookmarks.get(stream_id)


def get_abs_path(path):
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), path)


def load_schemas():
    """ Load schemas from schemas folder """
    schemas = {}
    for filename in os.listdir(get_abs_path('schemas')):
        path = get_abs_path('schemas') + '/' + filename
        file_raw = filename.replace('.json', '')
        with open(path) as file:
            schemas[file_raw] = Schema.from_dict(json.load(file))
    return schemas


def create_metadata_for_report(stream_id, schema, key_properties):
    replication_key = get_bookmark(stream_id)
    mdata = [{"breadcrumb": [], "metadata": {"inclusion": "available", "forced-replication-method": "FULL_TABLE"}}]

    if key_properties:
        mdata[0]["metadata"]["table-key-properties"] = key_properties

    if stream_id in INCREMENTAL_SYNC_STREAMS:
        mdata[0]["metadata"]["forced-replication-method"] = "INCREMENTAL"
        mdata[0]["metadata"]["valid-replication-keys"] = [replication_key]

    for key in schema.properties:
        # hence, when property is object, we will only consider properties of that object without taking object itself.
        if "object" in schema.properties.get(key).type and schema.properties.get(key).properties:
            inclusion = "available"
            mdata.extend(
                [{"breadcrumb": ["properties", key, "properties", prop], "metadata": {"inclusion": inclusion}} for prop
                 in schema.properties.get(key).properties])
        else:
            inclusion = "automatic" if key in key_properties + [replication_key] else "available"
            mdata.append({"breadcrumb": ["properties", key], "metadata": {"inclusion": inclusion}})

    return mdata


def discover():
    raw_schemas = load_schemas()
    streams = []
    for stream_id, schema in raw_schemas.items():
        key_properties = get_key_properties(stream_id)
        stream_metadata = create_metadata_for_report(stream_id, schema, key_properties)
        streams.append(
            CatalogEntry(
                tap_stream_id=stream_id,
                stream=stream_id,
                schema=schema,
                key_properties=key_properties,
                metadata=stream_metadata
            )
        )
    return Catalog(streams)


def requests_session(session=None):
    """
    Creates or configures an HTTP session to use retries
    Returns:
        The configured HTTP session object
    """
    session = session or requests.Session()
    return session


@backoff.on_exception(backoff.expo, FreshcallerRateLimitError, max_tries=5, factor=2)
@utils.ratelimit(100, 60)
def make_request(session, url, parameters, headers):
    response = session.get(url, headers=headers, params=parameters)

    if response.status_code == 429:
        raise FreshcallerRateLimitError(response.text)
    elif response.status_code != 200:
        raise Exception(response.text)

    return response


def request_data(tap_stream_id, headers, parameters, config, session=None):
    url = HOST.format(domain=config["domain"]) + END_POINTS[tap_stream_id]
    session = requests_session(session)
    all_items = []
    parameters["page"] = 1
    total_pages = 1
    while parameters["page"] <= total_pages:
        response = make_request(session, url, parameters, headers)
        res = response.json()
        parameters["page"] += 1
        meta = res["meta"]
        if total_pages == 1:
            total_pages = meta["total_pages"]
        all_items += res[tap_stream_id]
    return all_items

def get_next_date(_date: str):
    return str(parse_datetime(_date) + datetime.timedelta(days=1))

def sync_incremental(config, state, stream):
    bookmark_column = get_bookmark(stream.tap_stream_id)
    mdata = metadata.to_map(stream.metadata)
    schema = stream.schema.to_dict()

    singer.write_schema(
        stream_name=stream.tap_stream_id,
        schema=schema,
        key_properties=stream.key_properties,
    )
    headers = {"accept": "application/json",
               "X-Api-Auth": config["api_key"]}
    bookmark = singer.get_bookmark(state, stream.tap_stream_id, bookmark_column) \
        if state.get("bookmarks", {}).get(stream.tap_stream_id) \
        else config["start_date"]

    session = requests_session()
    today = str(datetime.datetime.now(datetime.timezone.utc).date())

    while True:
        next_date = get_next_date(bookmark)
        params = {
            "by_time[from]": bookmark,
            "by_time[to]": next_date,
            "per_page": PAGE_RECORDS_LIMIT,
        }
        LOGGER.info("Querying Date --> from: %s, to: %s ", bookmark, next_date)
        tap_data = request_data(stream.tap_stream_id, headers, params, config, session=session)
        with singer.metrics.record_counter(stream.tap_stream_id) as counter:
            for row in tap_data:
                # Type Conversation and Transformation
                transformed_data = transform(row, schema, metadata=mdata)

                # write one or more rows to the stream:
                singer.write_records(stream.tap_stream_id, [transformed_data])
                counter.increment()
                bookmark = max([bookmark, row[bookmark_column]])

        state = singer.write_bookmark(state, stream.tap_stream_id, bookmark_column, bookmark)
        singer.write_state(state)

        if bookmark <= today:
            bookmark = next_date
        if bookmark > today:
            break


def sync_full_table(config, state, stream):
    mdata = metadata.to_map(stream.metadata)
    schema = stream.schema.to_dict()

    singer.write_schema(
        stream_name=stream.tap_stream_id,
        schema=schema,
        key_properties=stream.key_properties,
    )
    headers = {"accept": "application/json",
               "X-Api-Auth": config["api_key"]}
    session = requests_session()

    tap_data = request_data(stream.tap_stream_id, headers, parameters={}, config=config, session=session)

    with singer.metrics.record_counter(stream.tap_stream_id) as counter:
        for row in tap_data:
            # Type Conversation and Transformation
            transformed_data = transform(row, schema, metadata=mdata)

            # write one or more rows to the stream:
            singer.write_records(stream.tap_stream_id, [transformed_data])
            counter.increment()


def sync(config, state, catalog):
    # Loop over selected streams in catalog
    for stream in catalog.get_selected_streams(state):
        LOGGER.info("Syncing stream:" + stream.tap_stream_id)

        if stream.tap_stream_id in INCREMENTAL_SYNC_STREAMS:
            sync_incremental(config, state, stream)
        else:
            sync_full_table(config, state, stream)
    return


@utils.handle_top_exception(LOGGER)
def main():
    # Parse command line arguments
    args = utils.parse_args(REQUIRED_CONFIG_KEYS)

    if args.discover:
        catalog = discover()
        catalog.dump()
    else:
        if args.catalog:
            catalog = args.catalog
        else:
            catalog = discover()
        sync(args.config, args.state, catalog)


if __name__ == "__main__":
    main()
