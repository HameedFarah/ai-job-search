/* external-links.js — reliable external-tab policy for the Career Engine dashboard */
'use strict';

/*
 * With noopener/noreferrer, browsers are allowed to return null from
 * window.open() even when the new tab opened successfully. Treating that
 * return value as proof of popup blocking creates a false warning panel.
 *
 * The dashboard deliberately keeps external actions manual: this helper only
 * opens the requested URL in a separate tab and never submits/sends anything.
 */
function openCareerExternalTab(url) {
  if (!url) return null;
  try {
    return window.open(url, '_blank', 'noopener,noreferrer');
  } catch {
    return null;
  }
}

window.openInNewTab = openCareerExternalTab;
window.openGmailCompose = role => openCareerExternalTab(gmailComposeUrl(role));

/* Disable the legacy false-positive fallback if an older shared.js is loaded. */
window.showGmailBlocked = () => {};

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.gmail-blocked').forEach(panel => panel.remove());
});
