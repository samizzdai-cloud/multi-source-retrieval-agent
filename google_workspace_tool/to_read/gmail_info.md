# Gmail API overview

The Gmail API is a RESTful API that can be used to access Gmail
mailboxes and send mail. For most web applications the Gmail API is
the best choice for authorized access to a user's Gmail data and
is suitable for various applications, such as:

- Read-only mail extraction, indexing, and backup
- Automated or programmatic message sending
- Email account migration
- Email organization including filtering and sorting of messages
- Standardization of email signatures across an organization

## Common terms

The following is a list of common terms used in this API:

*Delegator and Delegate*
:   A delegator is a Gmail user who grants mailbox access to another user within
    the same Google Workspace organization. The user receiving this access is
    the delegate. Delegates can read, send, and delete messages, as well as view
    and add contacts on behalf of the delegator's account. For more information,
    see [Manage delegates](https://developers.google.com/workspace/gmail/api/guides/delegate_settings).

*Draft*
:   An unsent message. A message contained within the draft can be replaced.
    Sending a draft automatically deletes the draft and creates a message with
    the `SENT` system label. A draft is represented by the
    [`drafts`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts) resource.
    For more information, see [Work with
    drafts](https://developers.google.com/workspace/gmail/api/guides/drafts).

*Filters*
:   Advanced rules configured for an account that evaluate incoming messages
    against specific matching criteria (like sender, subject, or size). When a
    message matches, filters automatically trigger actions such as adding or
    removing labels, or forwarding the email to a specified address. For more
    information, see [Manage
    filters](https://developers.google.com/workspace/gmail/api/guides/filter_settings).

*Forwarding addresses*
:   Specific, verified email addresses configured to receive auto-forwarded
    messages from a user's account. Before an address can be used in a
    forwarding rule or filter, it must be officially registered and verified to
    prove ownership. For more information, see [Manage
    forwarding](https://developers.google.com/workspace/gmail/api/guides/forwarding_settings).

*Label*

:   A mechanism for organizing messages and threads. For example, the label
    "taxes" might be created and applied to all messages and threads having to
    do with a user's taxes. For more information, see [Manage
    labels](https://developers.google.com/workspace/gmail/api/guides/labels).

    There are two types of labels:

    *System labels*
    :   Internally-created labels, such as `INBOX`, `TRASH`, or `SPAM`. These
        labels cannot be deleted or modified. However, some system labels, such as
        `INBOX` can be applied to, or removed from, messages and threads.

    *User labels*
    :   Labels created by a user. These labels can be deleted or modified by the
        user or an application. A user label is represented by the
        [`labels`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels) resource.

*Message*

:   An email message containing the sender, recipients, subject, and body. Once a
    message is created, it cannot be changed. A message is represented by the
    [`messages`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages)
    resource.

*Push notifications*

:   A server-side notification system integrating with Google Cloud Pub/Sub.
    Applications can "watch" a mailbox, and the Gmail API will send automatic
    webhooks or alerts whenever a change occurs (like a new email arriving),
    eliminating the need for the app to continuously poll the server for
    updates. For more information, see [Push
    notifications](https://developers.google.com/workspace/gmail/api/guides/push).

*S/MIME certificates*

:   Digital certificates uploaded to specific send-as aliases that allow a user to
    send encrypted and digitally signed emails, ensuring message security and
    sender authenticity. For more information, see [Manage S/MIME
    certificates](https://developers.google.com/workspace/gmail/api/guides/smime_certs).

*Send-as aliases*

:   These represent the various email addresses from which an account is
    authorized to send mail. Every account has at least one alias representing
    the primary email address. Aliases correspond to the "Send mail as" feature
    in the Gmail web interface and are also the basis for configuring
    per-address email signatures. For more information, see [Manage
    aliases](https://developers.google.com/workspace/gmail/api/guides/alias_and_signature_settings).

*Thread*

:   A collection of related messages forming a conversation. In an email client
    app, a thread is formed when one or more recipients respond to a message
    with their own message. For more information, see [Manage
    threads](https://developers.google.com/workspace/gmail/api/guides/threads).

## Related topics

- To learn about developing with Google Workspace APIs, including handling
  authentication and authorization, refer to [Develop on
  Google Workspace](https://developers.google.com/workspace/guides/getstarted-overview).

- To learn how to configure and run a Gmail API app, read the
  [Quickstarts](https://developers.google.com/workspace/gmail/api/quickstart/js).





# Python quickstart

Create a Python command-line application that makes requests to the
Gmail API.

Quickstarts explain how to set up and run an app that calls a
Google Workspace API. This quickstart uses a
simplified authentication approach that is appropriate for a testing
environment. For a production environment, we recommend learning about
[authentication and authorization](https://developers.google.com/workspace/guides/auth-overview)
before
[choosing the access credentials](https://developers.google.com/workspace/guides/create-credentials#choose_the_access_credential_that_is_right_for_you)
that are appropriate for your app.

This quickstart uses Google Workspace's recommended API client libraries
to handle some details of the authentication and authorization flow.

## Objectives

- Set up your environment.
- Install the client library.
- Set up the sample.
- Run the sample.

## Prerequisites

To run this quickstart, you need the following prerequisites:

- Python 3.10.7 or greater
- The [pip](https://pypi.python.org/pypi/pip) package management tool
- [A Google Cloud project](https://developers.google.com/workspace/guides/create-project).

<br />

- A Google account with Gmail enabled.

<br />

## Set up your environment

To complete this quickstart, set up your environment.

### Enable the API

Before using Google APIs, you need to turn them on in a Google Cloud project. You can turn on one or more APIs in a single Google Cloud project.

- In the Google Cloud console, enable the Gmail API.

  [Enable the API](https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com)

### Configure the OAuth consent screen

If you're using a new Google Cloud project to complete this quickstart, configure
the OAuth consent screen. If you've already
completed this step for your Cloud project, skip to the next section.

1. In the Google API Console, go to Menu \> **Google Auth platform** \> **Branding** .

   [Go to Branding](https://console.developers.google.com/auth/branding)
2. If you have already configured the Google Auth platform, you can configure the following OAuth Consent Screen settings in [Branding](https://console.developers.google.com/auth/branding), [Audience](https://console.developers.google.com/auth/audience), and [Data Access](https://console.developers.google.com/auth/scopes). If you see a message that says **Google Auth platform not configured yet** , click **Get Started**:
   1. Under **App Information** , in **App name**, enter a name for the app.
   2. In **User support email**, choose a support email address where users can contact you if they have questions about their consent.
   3. Click **Next**.
   4. Under **Audience** , select **Internal**.
   5. Click **Next**.
   6. Under **Contact Information** , enter an **Email address** where you can be notified about any changes to your project.
   7. Click **Next**.
   8. Under **Finish** , review the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy) and if you agree, select **I agree to the Google API Services: User Data Policy**.
   9. Click **Continue**.
   10. Click **Create**.
3. For now, you can skip adding scopes. In the future, when you create an app for use outside of your Google Workspace organization, you must change the **User type** to **External** . Then add the authorization scopes that your app requires. To learn more, see the full [Configure OAuth consent](https://developers.google.com/workspace/guides/configure-oauth-consent) guide.

### Authorize credentials for a desktop application

To authenticate end users and access user data in your app, you need to create one or more OAuth 2.0 Client IDs. A client ID is used to identify a single app to Google's OAuth servers. If your app runs on multiple platforms, you must create a separate client ID for each platform.

1. In the Google API Console, go to Menu \> **Google Auth platform** \> **Clients** .

   [Go to Clients](https://console.developers.google.com/auth/clients)
2. Click **Create Client**.
3. Click **Application type** \> **Desktop app**.
4. In the **Name** field, type a name for the credential. This name is only shown in the Google API Console.
5. Click **Create** .


   The newly created credential appears under "OAuth 2.0 Client IDs."
6. Save the downloaded JSON file as `credentials.json`, and move the file to your working directory.

## Install the Google client library

- Install the Google client library for Python:

      python3 -m pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib

## Configure the sample

1. In your working directory, create a file named `quickstart.py`.
2. Include the following code in `quickstart.py`:


   gmail/quickstart/quickstart.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/gmail/quickstart/quickstart.py)

   ```python
   import os.path

   from google.auth.transport.requests import Request
   from google.oauth2.credentials import Credentials
   from google_auth_oauthlib.flow import InstalledAppFlow
   from googleapiclient.discovery import build
   from googleapiclient.errors import HttpError

   # If modifying these scopes, delete the file token.json.
   SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


   def main():
     """Shows basic usage of the Gmail API.
     Lists the user's Gmail labels.
     """
     creds = None
     # The file token.json stores the user's access and refresh tokens, and is
     # created automatically when the authorization flow completes for the first
     # time.
     if os.path.exists("token.json"):
       creds = Credentials.from_authorized_user_file("token.json", SCOPES)
     # If there are no (valid) credentials available, let the user log in.
     if not creds or not creds.valid:
       if creds and creds.expired and creds.refresh_token:
         creds.refresh(Request())
       else:
         flow = InstalledAppFlow.from_client_secrets_file(
             "credentials.json", SCOPES
         )
         creds = flow.run_local_server(port=0)
       # Save the credentials for the next run
       with open("token.json", "w") as token:
         token.write(creds.to_json())

     try:
       # Call the Gmail API
       service = build("gmail", "v1", credentials=creds)
       results = service.users().labels().list(userId="me").execute()
       labels = results.get("labels", [])

       if not labels:
         print("No labels found.")
         return
       print("Labels:")
       for label in labels:
         print(label["name"])

     except HttpError as error:
       # TODO(developer) - Handle errors from gmail API.
       print(f"An error occurred: {error}")


   if __name__ == "__main__":
     main()
   ```

   <br />

   <br />

## Run the sample

1. In your working directory, build and run the sample:

       python3 quickstart.py

<!-- -->

2. The first time you run the sample, it prompts you to authorize access:
   1. If you're not already signed in to your Google Account, sign in when prompted. If you're signed in to multiple accounts, select one account to use for authorization.
   2. Click **Accept**.


   Your Python application runs and calls the Gmail API.


   Authorization information is stored in the file system, so the next time you run the sample
   code, you aren't prompted for authorization.

## Next steps

- [Try the Google Workspace APIs in the APIs explorer](https://developers.google.com/workspace/explore)

<!-- -->

- [Troubleshoot authentication and authorization issues](https://developers.google.com/workspace/gmail/api/troubleshoot-authentication-authorization)
- [Gmail API reference documentation](https://developers.google.com/workspace/gmail/api/reference/rest)
- [Google APIs Client for Python documentation](https://developers.google.com/api-client-library/python)
- [Gmail API PyDoc documentation](https://developers.google.com/resources/api-libraries/documentation/gmail/v1/python/latest/index%2Ehtml)


# Manage threads

This document explains how to retrieve thread messages and add messages to
threads using the Gmail API.

The Gmail API uses the
[`threads`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads) resource to
group email replies with their original message into a single conversation or
thread. This lets you retrieve all messages in a conversation, in order, which
makes it easier to have context for a message or to refine search results.

Like the [`messages`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages)
resource, threads can also have labels applied to them. However, unlike
messages, threads cannot be created, only deleted. Messages can, however, be
inserted into a thread.

## Retrieve threads

Threads provide a way of retrieving messages in a conversation, in order. By
listing a set of threads you can choose to group messages by conversation and
provide additional context. You can retrieve a list of threads using the
[`threads.list`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/list)
method, or retrieve a specific thread with the
[`threads.get`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/get)
method.

The following code sample shows how to use the `threads.get` and `threads.list`
methods in a sample that retrieves the most chatty threads in your inbox. The
`threads.list` method fetches all thread IDs, then `threads.get` grabs all
messages in each thread. For messages with three or more replies, we extract the
`Subject` line and display the non-empty ones plus the number of messages in the
thread.

### Python

gmail/snippet/thread/threads.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/gmail/snippet/thread/threads.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def show_chatty_threads():
  """Display threads with long conversations(>= 3 messages)
  Return: None

  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()

  try:
    # create gmail api client
    service = build("gmail", "v1", credentials=creds)

    # pylint: disable=maybe-no-member
    # pylint: disable:R1710
    threads = (
        service.users().threads().list(userId="me").execute().get("threads", [])
    )
    for thread in threads:
      tdata = (
          service.users().threads().get(userId="me", id=thread["id"]).execute()
      )
      nmsgs = len(tdata["messages"])

      # skip if <3 msgs in thread
      if nmsgs > 2:
        msg = tdata["messages"][0]["payload"]
        subject = ""
        for header in msg["headers"]:
          if header["name"] == "Subject":
            subject = header["value"]
            break
        if subject:  # skip if no Subject line
          print(f"- {subject}, {nmsgs}")
    return threads

  except HttpError as error:
    print(f"An error occurred: {error}")


if __name__ == "__main__":
  show_chatty_threads()
```

You can also [filter threads](https://developers.google.com/workspace/gmail/api/guides/filtering) using the
same query parameters as those used for the
[`messages`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages) resource. If
any message in a thread matches the query, the thread is returned in the result.

## Add drafts and messages to threads

If you're sending or migrating messages that are a response to another email or
part of a conversation, your application should add that message to the related
thread. This makes it easier for Gmail users who are participating in
the conversation to keep the message in context.

A draft can be added to a thread as part of
[creating](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create),
[updating](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/update), or
[sending](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/send) a message
using the [`drafts`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts)
resource.

You can also add a message to a thread as part of
[inserting](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/insert) or
[sending](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send) a message
using the [`messages`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages)
resource.

To be part of a thread, a draft or message must meet the following criteria:

1. The requested
   [`threadId`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages#Message.FIELDS.thread_id)
   must be specified as part of the
   [`drafts.message`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts#Draft.FIELDS.message)
   or `messages` resource you supply with your request.

2. The `References` and `In-Reply-To` headers must be set in compliance with
   the [RFC 2822](https://datatracker.ietf.org/doc/html/rfc2822#appendix-A.2)
   standard.

3. The `Subject` headers must match.

For code samples of how to use the `threadId`, see [Create
drafts](https://developers.google.com/workspace/gmail/api/guides/drafts#create-drafts) or [Send
messages](https://developers.google.com/workspace/gmail/api/guides/sending#send-messages). In both cases, you
need to include the target `threadId` within the `messages` resource of your
request.

## Related topics

- [Create and send email messages](https://developers.google.com/workspace/gmail/api/guides/sending)
- [Troubleshoot authentication \& authorization issues](https://developers.google.com/workspace/gmail/api/troubleshoot-authentication-authorization)



# Manage labels

This document explains how to manage labels on threads and messages using the
Gmail API.

You can use labels to tag, organize, and categorize messages and threads in
Gmail. A label has a many-to-many relationship with messages and
threads: you can apply multiple labels to a single message or thread, and apply
a single label to multiple messages or threads.

For information on how to
[create](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/create),
[get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/get),
[list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/list),
[update](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/update), or
[delete](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/delete) labels, see
the [`labels`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels) resource.

To manage labels, you must use the
`https://www.googleapis.com/auth/gmail.labels` non-sensitive scope. For more
information about scopes, see [Choose Gmail API
scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).

## Types of labels

Labels come in two varieties: reserved `SYSTEM` labels and custom `USER` labels.

System labels typically correspond to predefined elements in the
Gmail web interface such as `INBOX`. System label names are
reserved; no `USER` label can be created with the same name as any `SYSTEM`
label. The following table lists several of the most common Gmail
system labels:

| Name | Can be manually applied | Notes |
|---|---|---|
| `INBOX` | yes |   |
| `SPAM` | yes |   |
| `TRASH` | yes |   |
| `UNREAD` | yes |   |
| `STARRED` | yes |   |
| `IMPORTANT` | yes |   |
| `SENT` | no | Applied automatically to messages that are: - sent with [`drafts.send`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/send) or [`messages.send`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send). - inserted with [`messages.insert`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/insert) and the user's email in the `From` header. - sent by the user through the web interface. |
| `DRAFT` | no | Automatically applied to all [`draft`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts) messages created with the Gmail API or Gmail interface. |
| `CATEGORY_PERSONAL` | yes | Corresponds to messages that are displayed in the Personal tab of the Gmail interface. |
| `CATEGORY_SOCIAL` | yes | Corresponds to messages that are displayed in the Social tab of the Gmail interface. |
| `CATEGORY_PROMOTIONS` | yes | Corresponds to messages that are displayed in the Promotions tab of the Gmail interface. |
| `CATEGORY_UPDATES` | yes | Corresponds to messages that are displayed in the Updates tab of the Gmail interface. |
| `CATEGORY_FORUMS` | yes | Corresponds to messages that are displayed in the Forums tab of the Gmail interface. |

> [!NOTE]
> **Note:** The preceding list isn't exhaustive and other reserved label names exist. Attempting to create a custom label with a name that conflicts with a reserved name results in an `HTTP 400 - Invalid label name` error.

## Manage labels on threads and messages

Labels only exist on messages. If you retrieve a list of labels on a thread, you
get a list of labels that exist on any message within the thread. A label might
not exist on every message within a thread.

You can't apply labels to draft messages.

### Add or remove labels on threads

When you add or remove a label on a thread, you add or remove the specified
label on all existing messages in the thread.

If messages are added to a thread after you add a label, the new messages don't
inherit the existing label associated with the thread. To add the label to those
messages, add the label to the thread again.

To add or remove the labels associated with a thread, use
[`threads.modify`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/modify).

### Add or remove labels on messages

When you add a label to a message, the label is added to that message and
becomes associated with the thread to which the message belongs. The label isn't
added to other messages within the thread.

If you remove a label from a message and it was the only message in the thread
with that label, the label is also removed from the thread.

To add or remove the labels applied to a message, use
[`messages.modify`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify).



# Search and filter messages

This document explains how to query and find messages using the Gmail API.

You can search or filter files using the
[`messages.list`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
and [`threads.list`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/list)
methods. These methods accept the `q` query parameter, which supports most of the
same [advanced search syntax](https://support.google.com/mail/answer/7190) as
the Gmail web interface. For a list of search and filter differences
between the Gmail UI and Gmail API, see [Differences from the
Gmail UI](https://developers.google.com/workspace/gmail/api/guides/filtering#differences).

This advanced search syntax lets you use queries to filter messages by
properties such as the sender, date, or label. For example, the following
`messages.list` method query retrieves all messages sent by the user in January
2014:

```
GET https://www.googleapis.com/gmail/v1/users/me/messages?q=in:sent after:2014/01/01 before:2014/02/01
```

> [!CAUTION]
> **Caution:** All dates used in the search query are interpreted as midnight on that date in the PST timezone. To specify accurate dates for other timezones pass the value in seconds instead:
>
> ```
> ?q=in:sent after:1388552400 before:1391230800
> ```

In addition to search queries, you can also filter messages and threads by label
with the `labelIds[]` parameter. This lets you search for messages and threads
with the specified system or user labels applied. For more information, see the
`messages.list` and `threads.list` methods. For more information on labels, see
[Manage labels](https://developers.google.com/workspace/gmail/api/guides/labels).

## Differences from the Gmail UI

While the Gmail API supports most of the advanced search syntax used
in the Gmail UI, there are a few differences:

- The Gmail UI performs *alias expansion* , which allows it to
  infer an account alias from a Google Workspace account. For example,
  suppose you have an account of `myprimary@cymbalgroup.com` and your
  administrator sets up an alias for that account of
  `myalias@cymbalgroup.com`. If `myalias@cymbalgroup.com` sends an email, but
  you search for "`from: myprimary@cymbalgroup.com`" the email sent by
  `myalias@cymbalgroup.com` appears in search results in the
  Gmail UI, but not in the API response.

- The Gmail UI allows users to perform thread-wide searches,
  but the API doesn't.

## Related topics

- [List Gmail messages](https://developers.google.com/workspace/gmail/api/guides/list-messages)

# List Gmail messages

This document explains how to call the Gmail API's
[`messages.list`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
method.

The method returns an array of Gmail `messages` objects that
contain the message `id` and `threadId`. To retrieve full message details, use
the [`messages.get`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)
method.

## Prerequisites

### Python

A Google Cloud project with the Gmail API enabled. For steps, complete
the [Gmail API Python
quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python).

## List messages

The `messages.list` method supports several query parameters to filter the
messages:

- `maxResults`: Maximum number of messages to return (defaults to 100, maximum 500).
- `pageToken`: Token to retrieve a specific page of results.
- `q`: Query string to filter messages, such as `from:someuser@example.com
  is:unread`.
- `labelIds`: Only return messages with labels that match all specified label IDs.
- `includeSpamTrash`: Include messages from `SPAM` and `TRASH` in the results.

### Code sample

### Python

The following code sample shows how to list messages for the authenticated
Gmail user. The code handles pagination to retrieve all
messages matching the query.
gmail/snippet/list_messages.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/gmail/snippet/list_messages.py)

```python
import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    """Shows basic usage of the Gmail API.
    Lists the user's Gmail messages.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        # Call the Gmail API
        service = build("gmail", "v1", credentials=creds)
        results = (
            service.users().messages().list(userId="me", labelIds=["INBOX"]).execute()
        )
        messages = results.get("messages", [])

        if not messages:
            print("No messages found.")
            return

        print("Messages:")
        for message in messages:
            print(f'Message ID: {message["id"]}')
            msg = (
                service.users().messages().get(userId="me", id=message["id"]).execute()
            )
            print(f'  Subject: {msg["snippet"]}')

    except HttpError as error:
        # TODO(developer) - Handle errors from gmail API.
        print(f"An error occurred: {error}")


if __name__ == "__main__":
    main()
```

The `messages.list` method returns a response body that contains the following:

- `messages[]`: An array of `Message` resources.
- `nextPageToken`: For requests with multiple pages of results, a token that can be used with subsequent calls to list more messages.
- `resultSizeEstimate`: An estimated total number of results.

To fetch the full message content and metadata, use the `message.id` field to
call the
[`messages.get`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)
method.

## Related topics

- [Search and filter messages](https://developers.google.com/workspace/gmail/api/guides/filtering)

# Migrate from the Email Settings API

This document can help migrate your app from the deprecated [Email Settings
API](https://workspaceupdates.googleblog.com/2018/10/email-settings-api-shutdown.html)
to the Gmail API.

## Authorize requests

Like the Email Settings API, the Gmail API uses the OAuth 2.0 protocol
to authorize requests. One key difference is that Gmail API
permissions are scoped to an individual user, rather than to the entire domain.
This means authorizing a domain administrator account doesn't let you migrate
mail for other users in the domain. Instead, you must use [standard service
accounts with domain-wide authority](https://developers.google.com/identity/protocols/oauth2/service-account)
that are added to an allowlist in the
[Google Admin console](https://admin.google.com) to generate the appropriate
authentication token.

The Email Settings API used the scope:

    https://apps-apis.google.com/a/feeds/emailsettings/2.0/

The equivalent scopes in the Gmail API are:

    https://www.googleapis.com/auth/gmail.settings.basic
    https://www.googleapis.com/auth/gmail.settings.sharing

## Protocol changes

The Email Settings API uses the XML based GDATA protocol. The
Gmail API uses JSON. Since settings are mostly comprised of key-value
pairs, payloads are conceptually similar between versions.

Example of creating a label:

### Email Settings API

    POST https://apps-apis.google.com/a/feeds/emailsettings/2.0/{domain name}/{username}/label
    <?xml version="1.0" encoding="utf-8"?>
    <atom:entry xmlns:atom="http://www.w3.org/2005/Atom" xmlns:apps="http://schemas.google.com/apps/2006">
      <apps:property name="label" value="status updates" />
    </atom:entry>

### Gmail API

    POST https://www.googleapis.com/gmail/v1/users/{username}/labels
    {
       "name": "status updates"
    }

Use the provided [client libraries](https://developers.google.com/workspace/gmail/api/downloads) instead of
directly implementing the protocol.

## Manage labels

To manage labels in the Gmail API, use the
[`labels`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels) resource.

| Old setting | New setting | Notes |
|---|---|---|
| labelId | id |   |
| label | name |   |
| unreadCount | messagesUnread |   |
| visibility | labelListVisibility | `SHOW` is now `labelShow` `HIDE` is now `labelHide` |

Other changes:

- When updating or deleting labels, the Gmail API references labels by ID instead of by name.

## Manage filters

To manage filters in the Gmail API, use the
[`settings.filters`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.filters)
resource.

| Old setting | New setting | Notes |
|---|---|---|
| from | criteria.from |   |
| to | criteria.to |   |
| subject | criteria.subject |   |
| hasTheWord | criteria.query |   |
| doesNotHaveTheWord | criteria.negatedQuery |   |
| hasAttachment | criteria.hasAttachment |   |
| shouldArchive | action.removeLabelIds | Use `INBOX` as the label ID |
| shouldMarkAsRead | action.removeLabelIds | Use `UNREAD` as the label ID |
| shouldStar | action.addLabelIds | Use `STARRED` as the label ID |
| label | action.addLabelIds | Use the ID of the label to add |
| forwardTo | action.forward |   |
| shouldTrash | action.addLabelIds | Use `TRASH` as the label ID |
| neverSpam | action.removeLabelIds | Use `SPAM` as the label ID |

Other changes:

- If adding a user label doesn't already exist, it must be explicitly created using the [`labels.create`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/create) method.

## Manage send-as aliases

To manage send-as aliases in the Gmail API, use the
[`settings.sendAs`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.sendAs) resource.

| Old setting | New setting |
|---|---|
| name | displayName |
| address | sendAsEmail |
| replyTo | replyToAddress |
| makeDefault | isDefault |

## Manage web clips

Web Clip settings are not available in the Gmail API.

## Manage automatic forwarding

To manage automatic forwarding in the Gmail API, use the
[`settings`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings) resource.

| Old setting | New setting | Notes |
|---|---|---|
| enable | enabled |   |
| forwardTo | emailAddress |   |
| action | disposition | `KEEP` is now `leaveInInbox` `ARCHIVE` is now `archive` `DELETE` is now `trash` `MARK_READ` is now `markRead` |

Other changes:

- Forwarding addresses must be created and verified prior to use.
- Forwarding addresses can be managed using the [`settings.forwardingAddresses`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.forwardingAddresses) resource.

## Manage POP settings

To manage POP access in the Gmail API, use the
[`settings`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings) resource.

| Old setting | New setting | Notes |
|---|---|---|
| enable | accessWindow | Disabled when set to `disabled` |
| enableFor | accessWindow | `ALL_MAIL` is now `allMail` `MAIL_FROM_NOW_ON` is now `fromNowOn` |
| action | disposition | `KEEP` is now `leaveInInbox` `ARCHIVE` is now `archive` `DELETE` is now `trash` `MARK_READ` is now `markRead` |

## Manage IMAP settings

To manage IMAP access in the Gmail API, use the
[`settings`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings) resource.

| Old setting | New setting |
|---|---|
| enable | enabled |

## Manage vacation automatic reply settings

To manage vacation automatic reply in the Gmail API, use the
[`settings`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings) resource.

| Old setting | New setting |
|---|---|
| contactsOnly | restrictToContacts |
| domainOnly | restrictToDomain |
| enable | enableAutoReply |
| endDate | endTime |
| message | responseBodyHtml responseBodyPlainText |
| startDate | startTime |
| subject | responseSubject |

## Manage signature settings

To manage email signatures in the Gmail API, use the
[`settings.sendAs`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.sendAs)
resource.

| Old setting | New setting |
|---|---|
| signature | signature |

Other changes:

- Signatures are now managed per alias.

## Manage language settings

To manage language settings in the Gmail API, use the
[`settings`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings) resource.

| Old setting | New setting |
|---|---|
| language | displayLanguage |

For more information, see [Manage language
settings](https://developers.google.com/workspace/gmail/api/guides/language-settings).

## Manage delegation settings

To manage delegation in the Gmail API, use the
[`settings.delegates`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates)
resource.

| Old setting | New setting |
|---|---|
| address | delegateEmail |
| status | verificationStatus |

Other changes:

- General
  - To use any of the delegation methods (including [`settings.delegates.create`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates/create)) the delegator user must be enabled for Gmail. That means, for example, that the delegator user cannot be suspended in Google Workspace.
  - An email alias cannot be used as the delegate email input for any of the new methods. A delegate user must be referred to by their primary email address.
- [`settings.delegates.create`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates/create)
  - This method can now be used to create delegate relationships across multiple domains belonging to the same Google Workspace organization.
  - This method can now be used for users which require a password change at their next sign in.
  - If successful, this method returns a [`settings.delegates`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates) resource in the response body, rather than an empty response body.
  - If either of the delegator or delegate users are disabled (for example, suspended in Google Workspace) this method fails with an HTTP 4XX error instead of an HTTP 500 error.
- [`settings.delegates.delete`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates/delete)
  - This method can now be used to delete delegates with any [`VerificationStatus`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates#verificationstatus), rather than just delegates which are `accepted` or `expired`.
- [`settings.delegates.get`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates/get)
  - This is a new method, which might be preferable over the [`settings.delegates.list`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings.delegates/list) method depending on need.

## Manage general settings

General settings are not available in the Gmail API.