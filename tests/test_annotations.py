"""What the bridge does with highlights and annotations, checked.

The README tells a reader of ebooks that highlights and annotations are not carried. These are
the two facts that sentence rests on: no annotation address is served or advertised, and the
only things kept from what the reader pushes are the reading position, the read status and the
reading statistics.
"""

from conftest import loads


def test_no_annotation_address_is_served_or_advertised(client, kobo_url):
    resources = loads(client.get(kobo_url("/v1/initialization")))["Resources"]
    named = [
        name
        for name, value in resources.items()
        if "annotation" in name.lower()
        or (isinstance(value, str) and "annotation" in value.lower())
    ]
    assert named == [], "the device is told about an address the bridge does not serve"
    # The per book address a reader of the protocol would look for first.
    assert client.get(kobo_url("/v1/library/some-uuid/annotations")).status_code == 404


def test_only_position_status_and_statistics_are_kept(client, kobo_url):
    """Anything else the reader pushes with its reading state is dropped, not stored."""
    book_uuid = loads(client.get(kobo_url("/v1/library/sync")))[0]["NewEntitlement"][
        "BookEntitlement"
    ]["Id"]
    url = kobo_url("/v1/library/{0}/state".format(book_uuid))
    client.put(
        url,
        json={
            "ReadingStates": [
                {
                    "CurrentBookmark": {"ProgressPercent": 42},
                    "Annotations": [{"Text": "a highlighted sentence"}],
                }
            ]
        },
    )
    stored = loads(client.get(url))[0]
    assert stored["CurrentBookmark"]["ProgressPercent"] == 42
    assert set(stored) == {
        "EntitlementId",
        "Created",
        "LastModified",
        "PriorityTimestamp",
        "StatusInfo",
        "Statistics",
        "CurrentBookmark",
    }
