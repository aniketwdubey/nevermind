# Recording the Nevermind demonstration

This is a recording guide, not a claim that a video has been recorded.

## Before recording

- Use an invited demonstration account and synthetic workshop data only.
- Sign in before starting the capture. Never record the password file, browser developer tools, request headers, or AWS console.
- Keep the frame on the application, not the browser address bar or local filesystem.
- Check that the prepared workshop is in person. Reset only your demonstration sandbox if needed; a reset invalidates existing previews.
- Model inference can take more than a minute. If editing out the wait, label the cut or speed-up. Do not imply instant inference.
- If clarification is required, show it honestly. Do not lower the threshold or replace a real decision just to make a recording look successful.

## Main sequence

Suggested edited length: two to three minutes.

| Scene | On screen | Suggested narration |
|---|---|---|
| The problem | Current in-person plan and six resources | "The workshop is prepared. Now the organizer changes their mind. Starting over would throw away useful work." |
| The request | Enter "Make it online. Same date and time." and click **Review changes** | "Nevermind checks which assumptions changed. Models propose updates, but they cannot execute them." |
| The review | Show the invitation's before/after text, room release, and retained slides | "The invitation and materials need updating. Slides and the unrelated sponsor note stay exactly as they are. Nothing has changed yet." |
| Standard approval | Click **Apply reviewed changes**, then show resource versions and **Action history** | "Approval applies to this exact preview and these resource versions. A changed plan requires another review." |
| Separate cancellation | Inspect the cancellation draft, approve it separately, then expand **Sent sandbox messages** | "The original catering request cannot be unsent. This separate approval adds a cancellation request, while preserving the original. Vendor acceptance is unknown." |
| Limits | Finish on the history or landing page | "This uses real Amazon Nova and Strands Decider inference on AWS. Bookings and messages are simulated. Uncertain decisions block approval." |

Review the exact cancellation text before clicking its approval button. It is not included in standard approval.

## Optional short clips

- **Hypothetical:** reset the demonstration sandbox, request "What if we made it online?", and show that applying is disabled.
- **Local attendees:** reset and request "Make it online, but keep the room for local attendees." Show that the room remains. If the review asks for clarification, show that result rather than hiding it.
- **Stale preview:** retain an old browser tab, reset in a different tab, and attempt the old approval. The server must reject it.

## Screenshots to capture

1. Public landing page, without browser chrome.
2. Ready review: invitation before/after and the resource list.
3. Approved changes: updated versions beside unchanged slides.
4. Separate cancellation: the draft and preserved original message in history.
5. Hypothetical or uncertainty: disabled approval and the visible explanation.

Only reviewed, credential-free screenshots should enter the repository. Keep raw recordings and temporary captures outside source control. Add a public video link to the README only after the owner has reviewed and approved publication.
