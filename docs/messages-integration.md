# Messages integration

How La Suite **Contacts** and La Suite **Messages**
([suitenumerique/messages](https://github.com/suitenumerique/messages)) fit
together.

There are four separate surfaces. They are independent: you can enable any
one without the others.

| Surface | Direction | Transport |
| ------- | --------- | --------- |
| Recipient suggestion | Messages reads | external API (user token) |
| Address book read/write | Messages reads and writes | CardDAV |
| "Send a message" deep link | Contacts links out | URL template |
| Feedback widget | Contacts embeds | Messages JS widget |

## 1. Recipient suggestion goes through the external API

Messages' backend suggests recipients with
`GET /external_api/v1.0/search/`, called with the access token its user
logged in with. Contacts must list `messages` in
`OIDC_RS_ALLOWED_AUDIENCES`, and Messages' tokens must name Contacts in
their audience. See [resource_server.md](resource_server.md).

It reports what happens to mail with `POST /external_api/v1.0/interactions/`,
by address (`{"email": …}`), so people rank higher next
time:

- **Sent** (`email_sent`, each To / Cc recipient), with the user's token;
- **Received** (`email_received`, the sender), from the server when mail
  arrives, with a channel holding `interactions:write` and `on_behalf_of`:

  ```bash
  python manage.py create_channel --name "Messages interactions" \
    --scope-level global --scopes interactions:write
  ```

An "Add to contacts" button on a sender calls
`POST /external_api/v1.0/contacts/`.

## 2. Address book over CardDAV

This is the useful integration for Messages: reading a user's contacts to
resolve a sender's name, and writing one back with a "save to contacts"
action.

Contacts exposes [CardDAV](carddav.md) at `/carddav/`. This section is the
Contacts-side configuration and the flow a service acting for its users
follows.

### Credential

Mint a **global** CardDAV channel:

```bash
python manage.py create_channel \
  --name "Messages" --type carddav --scopes carddav:write
```

It prints the channel and its password. The password is one opaque string.
The command shows it only once. Messages then authenticates with HTTP Basic
on every request:

```
username = the requesting user's OIDC identity email
password = the printed password (channel id + key, one opaque token)
```

against `https://<contacts host>/carddav/`.

### Trust model, read this before enabling

One secret authenticates Messages-as-a-service for **every** user; the
username picks which user it acts as. Any user authenticated by the IdP can
therefore cause Messages to act on the Contacts address book of whatever
email it puts in that username field.

The property that has to hold is that **the OIDC identity provider does not
let one person assert another person's `email` claim** (the claim is
verified, and not user-editable). Operators must confirm that for their IdP
before turning this on.

Contacts enforces what it can on its side: the email must match an active
user, an ambiguous address (`User.email` is not unique) is refused rather
than resolved arbitrarily, and deactivating an account immediately kills
every credential bound to it.

Prefer a `--scope-level organization` channel when Messages only ever acts
for one organization; the acting user is then required to belong to it.

### The flow

Examples use the dev stack (`http://localhost:8971`, demo user
`user1@example.local`). `$P` is the printed password. The `PROPFIND`
outputs show only the relevant part of each answer.

Discovery is RFC 6764, so a bare host name is enough:

```console
$ curl -o /dev/null -w "%{http_code} -> %{redirect_url}\n" \
    -X PROPFIND http://localhost:8971/.well-known/carddav
301 -> http://localhost:8971/carddav/
```

Find the principal for the acting user:

```console
$ curl -u "user1@example.local:$P" -X PROPFIND -H "Depth: 0" \
    --data '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>' \
    http://localhost:8971/carddav/
<d:current-user-principal><d:href>/carddav/principals/user1@example.local/</d:href></d:current-user-principal>
```

Then its address book home, and the books in it:

```console
$ curl -u "user1@example.local:$P" -X PROPFIND -H "Depth: 0" \
    --data '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav"><d:prop><c:addressbook-home-set/></d:prop></d:propfind>' \
    http://localhost:8971/carddav/principals/user1@example.local/
<card:addressbook-home-set><d:href>/carddav/addressbooks/</d:href></card:addressbook-home-set>

$ curl -u "user1@example.local:$P" -X PROPFIND -H "Depth: 1" \
    --data '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>' \
    http://localhost:8971/carddav/addressbooks/
/carddav/addressbooks/personal/
/carddav/addressbooks/directory/
```

`personal` is writable, `directory` holds read-only org and synced rows.

"Save this sender to my contacts" is a `PUT` of a fresh UUID into
`personal`, with `If-None-Match: *` so a retry cannot silently overwrite an
unrelated card:

```console
$ curl -u "user1@example.local:$P" -X PUT -H "If-None-Match: *" \
    -H "Content-Type: text/vcard" --data-binary @card.vcf \
    -o /dev/null -w "%{http_code}\n" \
    http://localhost:8971/carddav/addressbooks/personal/<uuid>.vcf
201

$ # same request again
412
```

To keep a local cache in step, use `sync-collection` rather than re-reading
the whole book. A token becomes invalid when `purge_carddav_changes`
drops a deletion that the token has not seen. The server then answers
`403 valid-sync-token`, and the client must sync again from scratch. See
[carddav.md](carddav.md) for the reports and their limits.

## 3. "Send a message" deep link

The contact card's mail action opens Messages instead of the OS mail
client, when configured:

```
FRONTEND_MESSAGES_COMPOSE_URL=https://<messages host>/<compose route>?to={email}
```

`{email}` is substituted with the URL-encoded primary address. Leave the
setting empty (the default) and the action falls back to `mailto:`. It is
served to the SPA by `GET /api/v1.0/config/`.

The setting is one URL for the whole deployment, so it needs a compose
route that does not depend on the user (Messages mailboxes are per user)
and prefills the recipient from the `to` parameter. Without such a route,
keep the setting empty.

## 4. Feedback widget

The feedback button (help menu) is shown when `FRONTEND_FEEDBACK_BUTTON_SHOW`
is true and `FRONTEND_FEEDBACK_BUTTON_IDLE` false. It then opens the
Messages feedback widget when that is enabled, otherwise a modal listing the
`FRONTEND_FEEDBACK_ITEMS` links (and the button is hidden when there are
none). All these settings are exposed through `GET /api/v1.0/config/`.

The widget needs all four of:

| Setting | Meaning |
| ------- | ------- |
| `FRONTEND_FEEDBACK_MESSAGES_WIDGET_ENABLED` | master switch, default `False` |
| `FRONTEND_FEEDBACK_MESSAGES_WIDGET_API_URL` | Messages API origin |
| `FRONTEND_FEEDBACK_MESSAGES_WIDGET_CHANNEL` | the public widget channel id on the Messages side |
| `FRONTEND_FEEDBACK_MESSAGES_WIDGET_PATH` | base URL of the widget scripts, with a trailing slash (the frontend loads `<path>feedback.js`) |

The frontend proxy must also allow the two origins: see
[Content-Security-Policy](deployment.md#content-security-policy).

## 5. When there is no service credential

Without a deployment-wide channel, a user can still sync their address book
to a device by minting a **user-scoped** CardDAV credential for themselves
over the API (`POST /api/v1.0/channels/`, which returns a ready-to-paste
`carddav_password`), or an operator can mint one:

```bash
python manage.py create_channel \
  --name "Phone" --type carddav --scope-level user \
  --user someone@example.org --scopes carddav:write
```

That credential only ever reaches that one user's data, so it carries none
of the trust caveat in section 2.
