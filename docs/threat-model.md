# Threat model and limits

## What it provides

* **Server location hidden.** The onion service does not reveal the server's
  IP / geographic location. No router port forwarding needed.
* **Encryption in transit.** Onion-to-onion traffic is end-to-end encrypted by
  Tor; even without STARTTLS the SMTP conversation is not visible in the clear
  on the network.
* **Closed network.** The server accepts mail only for its own address
  (`relay denied`). It is not an open relay.
* **Policy allowlist.** With `policy.allowlist_only = true`, mail is accepted
  only from known onion addresses — random onion spam is blocked.

## What it does not provide / caveats

* **Plaintext on disk.** Messages sit unencrypted in the Maildir on both
  servers. If you want content confidentiality, use the built-in
  **end-to-end encryption** (age; see `onionmail/crypto.py` and `compose.py`),
  or encrypt the body with PGP by hand on the client.
* **Metadata visible to the recipient.** The other server sees the sender
  address, time, size, and the Subject header. To protect the Subject too you
  need the end-to-end encrypted envelope (which hides it) or PGP/MIME.
* **Endpoint security.** If the server is compromised, all mail and the onion
  private key (`/var/lib/tor/onionmail/`) are exposed. That key = your address;
  back it up and restrict access (`chmod 700`, separate user).
* **No clearnet.** You cannot send to `gmail.com` and the like. Everyone you
  correspond with must run their own onion mail server.
* **Traffic analysis.** A global observer can attempt timing/volume
  correlation. Defeating that needs message-size padding / delays — not in this
  project.
* **The sandbox is not absolute.** `firejail --net=none` stops an attachment
  from reaching the network and from touching your home directory, but kernel
  bugs / firejail misconfiguration remain a risk. For genuinely suspicious
  things, use a throwaway VM. With `backend = "none"`, attachments are only
  extracted to disk, not opened.

## Safe defaults (code)

* smtpd listens on loopback only; `config.validate()` rejects 0.0.0.0.
* Outbound SOCKS uses `rdns=True` — the `.onion` name is not resolved locally,
  so there is no DNS leak.
* Attachment filenames are cleaned with `sanitize_filename()` (path traversal,
  control characters, whitespace); only the basename is used.
* The HTML body is not rendered with a real engine; `safe_text()` strips
  script/style, drops tags, and loads no remote content.
* Incoming message size is capped by `smtpd.max_message_bytes`.
