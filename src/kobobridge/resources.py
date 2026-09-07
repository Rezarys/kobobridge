"""The resource table the eReader asks for on ``/v1/initialization``.

The device expects a table of service addresses and feature flags, and settles into whatever it
is given. This one is built from the endpoints the bridge actually serves, not from a store
listing: every service entry points back at the bridge, and every store feature the bridge
cannot honour is turned off rather than advertised. A device pointed here therefore has no
reason to call the manufacturer's servers about your library at all.

Four addresses are the exception, and they are here on purpose: dictionaries, the user guide,
firmware discovery and sign in are device functions that have nothing to do with your library.
The bridge does not serve them, so the device keeps its own addresses for them. Drop them from
this table and those functions simply stop working.
"""

# Device functions the bridge has no business answering. Nothing about your library travels
# here: the device would reach these addresses with or without the bridge.
DEVICE_SERVICES = {
    "dictionary_host": "https://ereaderfiles.kobo.com",
    "userguide_host": "https://ereaderfiles.kobo.com",
    "discovery_host": "https://discovery.kobobooks.com",
    "oauth_host": "https://oauth.kobo.com",
}

# Store features the bridge cannot honour. Announcing them as unavailable is what keeps the
# device from asking, which is quieter than answering an empty object forever.
FEATURE_FLAGS = {
    "gpb_flow_enabled": "False",
    "instapaper_enabled": "False",
    "kobo_audiobooks_credit_redemption": "False",
    "kobo_audiobooks_enabled": "False",
    "kobo_audiobooks_orange_deal_enabled": "False",
    "kobo_audiobooks_subscriptions_enabled": "False",
    "kobo_display_price": "False",
    "kobo_dropbox_link_account_enabled": "False",
    "kobo_google_tax": "False",
    "kobo_googledrive_link_account_enabled": "False",
    "kobo_nativeborrow_enabled": "False",
    "kobo_onedrive_link_account_enabled": "False",
    "kobo_onestorelibrary_enabled": "False",
    "kobo_redeem_enabled": "False",
    "kobo_shelfie_enabled": "False",
    "kobo_subscriptions_enabled": "False",
    "kobo_superpoints_enabled": "False",
    "kobo_wishlist_enabled": "False",
    "use_one_store": "False",
}

# Everything the device may ask a library server for. The bridge answers all of these, either
# with real data or with an empty object, which is why they all point at the bridge. The value
# is the path under the bridge prefix; ``kobo.initialization`` turns it into a full address.
LIBRARY_SERVICES = {
    "add_entitlement": "/v1/library/{RevisionIds}",
    "affiliaterequest": "/v1/affiliate",
    "assets": "/v1/assets",
    "autocomplete": "/v1/products/autocomplete",
    "book": "/v1/products/books/{ProductId}",
    "categories": "/v1/categories",
    "configuration_data": "/v1/configuration",
    "content_access_book": "/v1/products/books/{ProductId}/access",
    "daily_deal": "/v1/products/dailydeal",
    "deals": "/v1/deals",
    "delete_entitlement": "/v1/library/{Ids}",
    "delete_tag": "/v1/library/tags/{TagId}",
    "delete_tag_items": "/v1/library/tags/{TagId}/items/delete",
    "device_auth": "/v1/auth/device",
    "device_refresh": "/v1/auth/refresh",
    "external_book": "/v1/products/books/external/{Ids}",
    "featured_lists": "/v1/products/featured",
    "fte_feedback": "/v1/products/ftefeedback",
    "funnel_metrics": "/v1/funnelmetrics",
    "get_download_keys": "/v1/library/downloadkeys",
    "get_download_link": "/v1/library/downloadlink",
    "get_tests_request": "/v1/analytics/gettests",
    "library_book": "/v1/user/library/books/{LibraryItemId}",
    "library_items": "/v1/library/sync",
    "library_metadata": "/v1/library/{Ids}/metadata",
    "library_prices": "/v1/user/library/previews/prices",
    "library_search": "/v1/library/search",
    "library_sync": "/v1/library/sync",
    "post_analytics_event": "/v1/analytics/event",
    "product_prices": "/v1/products/{ProductIds}/prices",
    "product_recommendations": "/v1/products/{ProductId}/recommendations",
    "product_reviews": "/v1/products/{ProductIds}/reviews",
    "products": "/v1/products",
    "productsv2": "/v2/products",
    "rating": "/v1/products/{ProductId}/rating/{Rating}",
    "reading_state": "/v1/library/{Ids}/state",
    "related_items": "/v1/products/{Id}/related",
    "remaining_book_series": "/v1/products/books/series/{SeriesId}",
    "rename_tag": "/v1/library/tags/{TagId}",
    "tag_items": "/v1/library/tags/{TagId}/Items",
    "tags": "/v1/library/tags",
    "taste_profile": "/v1/products/tasteprofile",
    "user_loyalty_benefits": "/v1/user/loyalty/benefits",
    "user_platform": "/v1/user/platform",
    "user_profile": "/v1/user/profile",
    "user_ratings": "/v1/user/ratings",
    "user_recommendations": "/v1/user/recommendations",
    "user_reviews": "/v1/user/reviews",
    "user_wishlist": "/v1/user/wishlist",
}

# Covers are served by the bridge from the Audiobookshelf cover endpoint.
IMAGE_TEMPLATES = {
    "image_url_template": "/{ImageId}/{Width}/{Height}/false/image.jpg",
    "image_url_quality_template": "/{ImageId}/{Width}/{Height}/{Quality}/{IsGreyscale}/image.jpg",
}


def build_resources(bridge_prefix, bridge_root):
    """The table to hand the device.

    ``bridge_prefix`` is the address of this bridge including the device token, and
    ``bridge_root`` is the same address without it, which is what the image host wants.
    """
    resources = dict(DEVICE_SERVICES)
    resources.update(FEATURE_FLAGS)
    for name, path in LIBRARY_SERVICES.items():
        resources[name] = bridge_prefix + path
    for name, path in IMAGE_TEMPLATES.items():
        resources[name] = bridge_prefix + path
    resources["image_host"] = bridge_root
    return resources
