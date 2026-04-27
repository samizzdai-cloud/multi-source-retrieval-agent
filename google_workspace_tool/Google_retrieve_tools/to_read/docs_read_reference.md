# Document

This guide introduces concepts such as the primary methods that make up the
Google Docs API, how to access a document, and the workflow when creating a
document.

## API methods

The [`documents`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) resource
provides methods you use to invoke the Docs API. The following methods
let you create, read, and update Docs documents:

- Use the [`documents.create`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/create) method to create a document.
- Use the [`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method to retrieve the contents of a specified document.
- Use the [`documents.batchUpdate`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate) method to atomically perform a set of updates on a specified document.

The `documents.get` and `documents.batchUpdate` methods require a `documentId`
as a parameter to specify the target document. The `documents.create` method
returns an instance of the created document, from which you can read the
`documentId`. For more information about Docs API requests and
response methods, see [Requests and
responses](https://developers.google.com/workspace/docs/api/concepts/request-response).

## Document ID

The `documentId` is the unique identifier for the document and it can be derived
from a document's URL. It's a particular string containing letters, numbers, and
some special characters. Document IDs are stable, even if the document name
changes.

    https://docs.google.com/document/d/DOCUMENT_ID/edit

The following regular expression can be used to extract the `documentId` from a
Google Docs URL:

```
/document/d/([a-zA-Z0-9-_]+)
```

If you're familiar with the Google Drive API, the `documentId` corresponds to `id`
in the [`files`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files) resource.

> [!TIP]
> **Tip:** If you're unsure what the `documentId` is for your file, experiment using the [Google APIs Explorer](https://developers.google.com/explorer-help/) on the [`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method to retrieve a JSON representation of your document.

## Manage documents in Google Drive

Docs files are stored in Google Drive, our cloud-based storage
service. While Docs API has its own standalone methods, it's often
necessary to also use Google Drive API methods to interact with a user's
Docs files. For example, to copy Docs files, use
Drive API's [`files.copy`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/copy)
method. For more information, see [Copy an existing
document](https://developers.google.com/workspace/docs/api/how-tos/documents#copy_an_existing_document).

By default, when using the Docs API a new document is saved to the
user's root folder on Drive. There are options for saving a file
to a Drive folder. For more information, see [Work with
Google Drive folders](https://developers.google.com/workspace/docs/api/how-tos/documents#work_with_folders).

### Work with Docs files

To retrieve a document from a user's My Drive, it's often
necessary to first use Drive's
[`files.list`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list) method to
retrieve the ID for a file. Calling the method without any parameters returns a
list of all files and folders, including the IDs, for the user.

A document's MIME type indicates the data type and format. The MIME type format
for Docs is `application/vnd.google-apps.document`. For a list of
MIME types, see [Google Workspace and Google Drive supported MIME
types](https://developers.google.com/workspace/drive/api/guides/mime-types).

To search by MIME type for just Docs files within My
Drive, append the following query string filter:

    q: mimeType = 'application/vnd.google-apps.document'

For more information about query string filters, see [Search for files and
folders](https://developers.google.com/workspace/drive/api/guides/search-files).

Once you know the `documentId`, use the
[`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method to
retrieve a complete instance of the specified document. For more information,
see [Requests and responses](https://developers.google.com/workspace/docs/api/concepts/request-response).

To export Google Workspace document byte content, use Drive's
[`files.export`](https://developers.google.com/drive/api/v3/reference/files/export) method with the
`documentId` of the file to export and the correct [export MIME
type](https://developers.google.com/workspace/drive/api/guides/ref-export-formats). For more information, see
[Export Google Workspace document content](https://developers.google.com/workspace/drive/api/guides/manage-downloads#export-content).

### Compare the `Get` and `List` methods

The following table describes the differences between the Drive
and Docs methods, and the data that's returned with each:

| Operator | Description | Usage |
|---|---|---|
| [`drive.files.get`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get) | Gets a file's metadata by ID. Returns an instance of the [`files`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files) resource. | Get the metadata for a specific file. |
| [`drive.files.list`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list) | Gets a user's files. Returns a list of files. | Get a list of user files when you're unsure which file you must modify. |
| [`docs.documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) | Gets the latest version of the specified document, including all formatting and text. Returns an instance of the [`documents`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) resource. | Get the document for a specific document ID. |

## Document creation workflow

Creating and populating a new document is straightforward, since there's no
existing content to worry about and there are no collaborators who can alter the
document state. Conceptually, this works as shown in the following sequence
diagram:
![Workflow to create and
populate a new document.](https://developers.google.com/static/workspace/docs/api/images/create-sequence.svg) **Figure 1.** Workflow to create and populate a new document.

In the Figure 1., a user interacting with the
[`documents`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) resource has the
following flow of information:

1. An app calls the [`documents.create`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/create) method on a web server.
2. The web server sends an HTTP response that contains an instance of the created document as a `documents` resource.
3. Optionally, the app calls the [`documents.batchUpdate`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate) method to atomically perform a set of edit requests to populate the document with data.
4. The web server sends an HTTP response. Some `documents.batchUpdate` methods provide a response body with information about the applied requests, whereas others surface an empty response.

## Document update workflow

Updating an existing document is more complex. Before you can make meaningful
calls to update a document, you must know its current state: what elements make
it up, what content is in those elements, and the order of the elements within
the document. The following sequence diagram shows how this works:
![Workflow to update a
document.](https://developers.google.com/static/workspace/docs/api/images/update-sequence.svg) **Figure 2.** Workflow to update a document.

In the Figure 2., a user interacting with the `documents` resource has the
following flow of information:

1. An app calls the [`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method on a web server, with the `documentId` of the file to find.
2. The web server sends an HTTP response that contains an instance of the specified document as a `documents` resource. The returned JSON contains the document content, formatting, and other features.
3. The app parses the JSON so the user can determine what content or format to update.
4. The app calls the `documents.batchUpdate` method to atomically perform a set of edit requests to update the document.
5. The web server sends an HTTP response. Some `documents.batchUpdate` methods provide a response body with information about the applied requests, whereas others surface an empty response.

This diagram doesn't consider workflows where concurrent updates by other
collaborators are made in the same document. For more information, see the best
practices section [Plan for
collaboration](https://developers.google.com/workspace/docs/api/how-tos/best-practices#plan-for-collaboration).

## Related topics

- [Structure of a Google Docs document](https://developers.google.com/workspace/docs/api/concepts/structure)
- [Requests and responses](https://developers.google.com/workspace/docs/api/concepts/request-response)
- [Structural edit rules and behavior](https://developers.google.com/workspace/docs/api/concepts/rules-behavior)
- [Best practices for best results](https://developers.google.com/workspace/docs/api/how-tos/best-practices)




# Extract the text from a document with Docs API

You might find it useful to extract only the text from a document. This is
helpful if you're passing the text to another API service. All the text in a
document is contained across its [tabs](https://developers.google.com/workspace/docs/api/how-tos/tabs) in
[text runs](https://developers.google.com/workspace/docs/api/concepts/structure#text-runs) of [paragraph
elements](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#paragraphelement).

Extracting all the text in a document involves traversing the tabs tree
hierarchy and calling the
[`get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method of the
[`documents`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) resource. The
`get` method includes the
[`includeTabsContent`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get#body.QUERY_PARAMETERS.include_tabs_content)
query parameter to configure whether
[`DocumentTab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#documenttab)
contents are provided in the response. For more information, see [Work with
Tabs](https://developers.google.com/workspace/docs/api/how-tos/tabs).

Text can appear in three types of the document tab's [structural
elements](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#structuralelement):

- Paragraph
- Table of Contents
- Tables

Tables can be nested inside another table. Therefore, to extract all the text in
a document, you must visit each nested structural element.

For a full description of the document body, see [Structure of a Google Docs
document](https://developers.google.com/workspace/docs/api/concepts/structure).

## Extract text code sample

The following Google Docs API code sample shows how to use recursion to visit each
structural element in all tabs of a document and prints the text.

> [!NOTE]
> You'll need to set up a project first so that you have credentials to make this work. For details, see [quickstarts](https://developers.google.com/workspace/docs/api/quickstart/python).


### Python


```python
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Recursively extracts the text from a Google Doc.
"""
import os.path

import googleapiclient.discovery as discovery
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = 'https://www.googleapis.com/auth/documents.readonly'
DISCOVERY_DOC = 'https://docs.googleapis.com/$discovery/rest?version=v1'
DOCUMENT_ID = 'YOUR_DOCUMENT_ID'


def get_credentials():
  """Gets valid user credentials from storage.

  If nothing has been stored, or if the stored credentials are invalid,
  the OAuth 2.0 flow is completed to obtain the new credentials.

  Returns:
      Credentials, the obtained credential.
  """
  creds = None
  # The file token.json stores the user's access and refresh tokens, and is
  # created automatically when the authorization flow completes for the first
  # time.
  if os.path.exists('token.json'):
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          'credentials.json', SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open('token.json', 'w') as token:
      token.write(creds.to_json())
  return creds


def add_current_and_child_tabs(tab, all_tabs):
  """Adds the provided tab to the list of all tabs, and recurses through and
  adds all child tabs.

  Args:
      tab: a Tab from a Google Doc.
      all_tabs: a list of all tabs in the document.
  """
  all_tabs.append(tab)
  for tab in tab.get('childTabs'):
    add_current_and_child_tabs(tab, all_tabs)


def get_all_tabs(doc):
  """Returns a flat list of all tabs in the document in the order they would
  appear in the UI (top-down ordering). Includes all child tabs.

  Args:
      doc: a document.
  """
  all_tabs = []
  # Iterate over all tabs and recursively add any child tabs to generate a
  # flat list of Tabs.
  for tab in doc.get('tabs'):
    add_current_and_child_tabs(tab, all_tabs)
  return all_tabs


def read_paragraph_element(element):
  """Returns the text in the given ParagraphElement.

  Args:
      element: a ParagraphElement from a Google Doc.
  """
  text_run = element.get('textRun')
  if not text_run:
    return ''
  return text_run.get('content')


def read_structural_elements(elements):
  """Recurses through a list of Structural Elements to read a document's text
  where text may be in nested elements.

  Args:
      elements: a list of Structural Elements.
  """
  text = ''
  for value in elements:
    if 'paragraph' in value:
      elements = value.get('paragraph').get('elements')
      for elem in elements:
        text += read_paragraph_element(elem)
    elif 'table' in value:
      # The text in table cells are in nested Structural Elements and tables may
      # be nested.
      table = value.get('table')
      for row in table.get('tableRows'):
        cells = row.get('tableCells')
        for cell in cells:
          text += read_structural_elements(cell.get('content'))
    elif 'tableOfContents' in value:
      # The text in the TOC is also in a Structural Element.
      toc = value.get('tableOfContents')
      text += read_structural_elements(toc.get('content'))
  return text


def main():
  """Uses the Docs API to print out the text of a document."""
  creds = get_credentials()

  try:
    docs_service = discovery.build(
        'docs', 'v1', credentials=creds, discoveryServiceUrl=DISCOVERY_DOC
    )
    # Fetch the document with all of the tabs populated, including any nested
    # child tabs.
    doc = (
        docs_service.documents()
        .get(documentId=DOCUMENT_ID, includeTabsContent=True)
        .execute()
    )
    all_tabs = get_all_tabs(doc)

    # Print the text from each tab in the document.
    for tab in all_tabs:
      # Get the DocumentTab from the generic Tab.
      document_tab = tab.get('documentTab')
      doc_content = document_tab.get('body').get('content')
      print(read_structural_elements(doc_content))
  except HttpError as error:
    # TODO(developer) - Handle errors from docs API.
    print(f'An error occurred: {error}')


if __name__ == '__main__':
  main()
```

<br />

# Structure of a Google Docs document

In order to use the Google Docs API effectively, you must understand the
architecture of a Google Docs document and the elements that make up a
document, as well as the relationship between them. This page provides a
detailed overview of these topics:

- A conceptual model of the document elements
- How the Docs API represents these elements
- The styling properties of the elements

> [!NOTE]
> Throughout this guide, Google presents a minimalist view of document structural elements. This is for simplicity and clarity. For a complete representation of the document structure, consider using the utility described under [Output document contents as JSON](https://developers.google.com/workspace/docs/api/samples/output-json).

## Top-level elements

The outermost container element in Google Docs is a *document*. This is the
unit that can be saved in Google Drive, shared with other users, and updated
with text and images.

The top-level elements of a
[`documents`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) resource include
its [`Tab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Tab)s,
[`SuggestionsViewMode`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#suggestionsviewmode),
and other attributes:

```
document: {
    title: ... ,
    revisionId: ... ,
    documentId: ... ,
    suggestionsViewMode: ... ,
    tabs: ...
}
```

> [!NOTE]
> There is a legacy representation of a document when the [includeTabsContent](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get#query-parameters) parameter is not specified or is false, which populates the text content fields (such as the Body content) with the first tab's contents directly under [Document](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents). For more information, see [documents.get](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get).

## Tabs

A single document can contain multiple [tabs](https://developers.google.com/workspace/docs/api/how-tos/tabs),
which have different text-level contents. The `tabs` property of document is a
sequence of `Tab` objects. A `Tab` is made up of the following fields:

- [`TabProperties`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#TabProperties): Contains a tab's attributes such as ID, title, and index.
- `childTabs`: Exposes a tab's child tabs (tabs that are nested directly beneath it).
- [`DocumentTab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#DocumentTab): Represents the text content of a tab.

![Structure of a document's tabs.](https://developers.google.com/static/workspace/docs/api/images/tabs.svg) **Figure 1.** Structure of a document's tabs.

The later sections give a brief overview of the document tab hierarchy; the [Tab
JSON representation](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Tab) also
provides more detailed information. See [Work with
Tabs](https://developers.google.com/workspace/docs/api/how-tos/tabs) for more information on the tabs
feature.

To manipulate global document tab features outside of the `Body` content, it's
almost always better to use one or more document templates, which you can use as
a basis for generating new documents programmatically. For more information, see
[Merge text into a document](https://developers.google.com/workspace/docs/api/how-tos/merge).

## Body content

The `Body` typically contains the full contents of a document's tab. Most of the
items you can, or would likely want to, use programmatically are elements within
the `Body` content:
![Structure of the body content.](https://developers.google.com/static/workspace/docs/api/images/top-classes.svg) **Figure 2.** Structure of the body content.

### Structural element

A [`StructuralElement`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#structuralelement)
describes content that provides structure to the document. The `Body` content is
a sequence of `StructuralElement` objects. A content element personalizes each
`StructuralElement` object, as shown in the following diagram:
![Structural elements.](https://developers.google.com/static/workspace/docs/api/images/mid-classes.svg) **Figure 3.** Structural elements.

Structural elements and their content objects contain all the visual components
within the document. This includes the text, inline images, and formatting.

> [!NOTE]
> The [`Header`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#header), [`Footer`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#footer), and [`Footnote`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#footnote) types have content elements that also contain a sequence of structural elements.

### Paragraph structure

A [`Paragraph`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#paragraph) is a
`StructuralElement` representing a paragraph. It has a range of content that's
terminated with a newline character. It's made up of the following objects:

- [`ParagraphElement`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#paragraphelement): Describes content within a paragraph.
- [`ParagraphStyle`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#paragraphstyle): An optional element that explicitly sets style properties for the paragraph.
- [`Bullet`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#bullet): If the paragraph is part of a list, an optional element that provides the bullet specification.

The `ParagraphElement` works something like a `StructuralElement`. A set of
content element types (such as
[`ColumnBreak`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#columnbreak) and
[`Equation`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#equation))
personalizes its own `ParagraphElement`, as shown in the following diagram:
![Structure of the paragraph elements.](https://developers.google.com/static/workspace/docs/api/images/para-classes.svg) **Figure 4.** Structure of the paragraph elements.

For an example of a complete document structure, see the [document
example](https://developers.google.com/workspace/docs/api/samples/output-json) in JSON format. In the output
you can see many of the key structural and content elements, as well as the use
of start and end indexes as described in a following section.

### Text runs

A [`TextRun`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#textrun) is a
[`ParagraphElement`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#paragraphelement)
that represents a contiguous string of text with all the same text style. A
paragraph can contain multiple text runs but text runs never cross paragraph
boundaries. Contents are split after a newline character to form separate text
runs. For example, consider a tiny document like the following:
![Small document with paragraphs and bulleted list.](https://developers.google.com/static/workspace/docs/api/images/simpledoc.png) **Figure 5.** A small document.

The following diagram shows how you might visualize the sequence of paragraphs
in the preceding document, each with its own `TextRun` and optional `Bullet`
settings.
![Structure of a text run.](https://developers.google.com/static/workspace/docs/api/images/textruns.svg) **Figure 6.** Structure of a text run.

### AutoText

[`AutoText`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#autotext) is a
`ParagraphElement` that represents a spot in text that's dynamically replaced
with content that can change over time. In Docs, this is used for
page numbers.

### Start and end indexes

When you make updates to the content of a document's tab, each update takes
place at a location or across a range within the document. These locations and
ranges are specified using *indexes* , which represent an offset within a
containing document *segment*. A segment is the body, header, footer, or
footnote containing structural or content elements. The indexes of the elements
within a segment are relative to the beginning of that segment.

Most elements within the body content have the zero-based `startIndex` and
`endIndex` properties. These indicate the offset of an element's beginning and
end, relative to the beginning of its enclosing segment. For more information
about how to order your batch Docs API calls, see [Batch
updates](https://developers.google.com/workspace/docs/api/concepts/request-response#batch-updates).

Indexes are measured in UTF-16 code units. This means surrogate pairs consume
two indexes. For example, the "GRINNING FACE" emoji, 😄, is represented as
`\uD83D\uDE00` and it consumes two indexes.

For elements within a document body, indexes represent offsets from the
beginning of the body content, which is the "root" element.

> [!NOTE]
> Elements not enclosed within content don't have these index properties. They're global in scope, and don't have the concept of position.

The "personalizing" types for structural
elements---[`SectionBreak`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#sectionbreak),
[`TableOfContents`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#tableofcontents),
[`Table`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#table), and
`Paragraph`---don't have these indexes because their enclosing
`StructuralElement` has these fields. This is also true of the personalizing
types contained in a `ParagraphElement`, such as `TextRun`, `AutoText`, and
[`PageBreak`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#pagebreak).

## Access elements

Many elements are modifiable with the
[`documents.batchUpdate`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate)
method. For example, using
[`InsertTextRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#inserttextrequest),
you can change the content of any element containing text. Similarly, you can
use
[`UpdateTextStyleRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatetextstylerequest)
to apply formatting to a range of text contained in one or more elements.

To read elements of the document, use the
[`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method to
obtain a JSON dump of the complete document. You can then parse the resulting
JSON to find the values of individual elements. For more information, see the
[Output document contents as JSON](https://developers.google.com/workspace/docs/api/samples/output-json).

Parsing the content can be beneficial for various use cases. Consider, for
example, a document cataloging application listing documents it finds. This app
can extract the title, revision ID, and starting page number of a document's
tabs, as shown in the following diagram:
![Structure of a document catalog.](https://developers.google.com/static/workspace/docs/api/images/other-nodes.svg) **Figure 7.** Structure of a document catalog.

Since there are no methods for reading these settings explicitly, your app needs
to get the whole document and then parse the JSON to extract these values.

## Property inheritance

A `StructuralElement` can inherit properties from its parent objects. An
object's properties, including those that it defines and those that it inherits,
determine its final visual appearance.

Text character formatting determines how text is rendered in a document, such as
bold, italic, and underline. The formatting that you apply overrides the default
formatting inherited from the underlying paragraph's
[`TextStyle`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#textstyle).
Conversely, any characters whose formatting you don't set continue to inherit
from the paragraph's styles.

Paragraph formatting determines how blocks of text are rendered in a document,
such as alignment, borders, and indentation. The formatting that you apply
overrides the default formatting inherited from the underlying `ParagraphStyle`.
Conversely, any formatting features that you don't set continue to inherit from
the paragraph style.

## Related topics

- [Requests and responses](https://developers.google.com/workspace/docs/api/concepts/request-response)
- [Structural edit rules and behavior](https://developers.google.com/workspace/docs/api/concepts/rules-behavior)




# Requests and responses

This guide introduces the primary request and response methods that make up the
Google Docs API and how you can update a document in batches.

You can invoke the Google Docs API using an HTTP request, or by using a method
invocation in a language-specific [client
library](https://developers.google.com/workspace/docs/api/how-tos/libraries). These are broadly equivalent.

The Google Docs API returns an HTTP response, which generally includes the result
of the request invocation. When using a client library to make requests, the
responses are returned in a language-specific way.

## Request methods

The Docs API supports the following methods:

- [`documents.create`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/create):
  Create a blank Google Docs document.

- [`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get):
  Return a complete instance of the specified document. You can parse the
  returned JSON to extract the document content, formatting, and other
  features.

- [`documents.batchUpdate`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate):
  Submit a list of editing requests to apply atomically to the document, and
  return a list of results.

The `documents.get` and `documents.batchUpdate` methods require a `documentId`
as a parameter to specify the target document. The `documents.create` method
returns an instance of the created document, from which you can read the
`documentId`. For more information about `documentId`, see [Document
ID](https://developers.google.com/workspace/docs/api/concepts/document#document-id).

## Published documents

You can't use the `documents.get` method to retrieve published documents. Once
published, public documents use a different URL format with a distinct, public
`documentId`. Attempts to use the public `documentId` with the `documents.get`
method return a `404` HTTP status code response.

Similarly, you can't use the Drive API
[`files.copy`](https://developers.google.com/workspace/drive/v3/reference/files/copy) method to copy a
published document.

To retrieve or copy a published document, you must use the original
`documentId`. There are no methods to extract the original `documentId` from a
published URL.

For more information, see:

- [Make Google Docs, Sheets, Slides \&
  Forms public](https://support.google.com/docs/answer/183965)
- [Copy an existing document](https://developers.google.com/workspace/docs/api/how-tos/documents#copy-existing-document)

## Batch updates

The
[`documents.batchUpdate`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate)
method takes a list of
[`request`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request) objects,
each one specifying a single request to perform. For example, format a paragraph
and then add an inline image. Each request is validated before being applied and
the requests are processed according to the order they appear in the batch
request.

All requests in the batch update are applied atomically. That is, if any request
isn't valid, then the entire update is unsuccessful and none of the (potentially
dependent) changes are applied.

> [!NOTE]
> Since other users might be editing your document, they could alter your changes. If there are no collaborators, the updated document should reflect your changes exactly. In either case, the updates are still performed atomically.

Some `documents.batchUpdate` methods provide responses with information about
the applied requests. These methods return a [response
body](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate#response-body)
that contains a list of
[`response`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/response) objects.
Other requests don't need to return information and surface an empty reply. The
objects in the response list occupy the same index order as the corresponding
request.

A popular pattern for making batch requests looks like this:

    requests = []
    requests.append(first request)
    requests.append(second request)
    ...

    body = ... & requests & ...

    ...batchUpdate(body)

See [batch request best practices](https://developers.google.com/workspace/docs/api/how-tos/batch) for full
details on how to batch your Docs API calls and the
[`documents.batchUpdate`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate)
reference documentation for request and response types.

> [!NOTE]
> You can---and should---use `batchUpdate` to group multiple requests into a single call where possible. This saves quota and can boost performance. For more information, see [Usage limits](https://developers.google.com/workspace/docs/api/limits).

### Batch update operations

There are various types of batch update requests. Here's a breakdown of the
[request types](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request),
grouped into different categories.

| Object | ADD / INSERT | UPDATE / REPLACE | DELETE |
|---|---|---|---|
| Text | [InsertTextRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#inserttextrequest) | [ReplaceAllTextRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#replacealltextrequest) |   |
| Styles | [CreateParagraphBulletsRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#createparagraphbulletsrequest) | [UpdateTextStyleRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatetextstylerequest) [UpdateParagraphStyleRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updateparagraphstylerequest) [UpdateTableCellStyleRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatetablecellstylerequest) [UpdateTableRowStyleRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatetablerowstylerequest) [UpdateDocumentStyleRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatedocumentstylerequest) [UpdateSectionStyleRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatesectionstylerequest) | [DeleteParagraphBulletsRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deleteparagraphbulletsrequest) |
| [Named Ranges](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#NamedRanges) | [CreateNamedRangeRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#createnamedrangerequest) | [ReplaceNamedRangeContentRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#replacenamedrangecontentrequest) | [DeleteNamedRangeRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deletenamedrangerequest) [DeleteContentRangeRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deletecontentrangerequest) |
| Images | [InsertInlineImageRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#insertinlineimagerequest) | [ReplaceImageRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#replaceimagerequest) |   |
| Tables | [InsertTableRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#inserttablerequest) [InsertTableRowRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#inserttablerowrequest) [InsertTableColumnRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#inserttablecolumnrequest) | [UpdateTableColumnPropertiesRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatetablecolumnpropertiesrequest) [MergeTableCellsRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#mergetablecellsrequest) [UnmergeTableCellsRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#unmergetablecellsrequest) [PinTableHeaderRowsRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#pintableheaderrowsrequest) | [DeleteTableRowRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deletetablerowrequest) [DeleteTableColumnRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deletetablecolumnrequest) |
| Page objects (including [headers](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Header) and [footers](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#footer)) | [InsertPageBreakRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#insertpagebreakrequest) [CreateHeaderRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#createheaderrequest) [CreateFooterRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#createfooterrequest) [CreateFootnoteRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#createfootnoterequest) [InsertSectionBreakRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#insertsectionbreakrequest) |   | [DeletePositionedObjectRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deletepositionedobjectrequest) [DeleteHeaderRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deleteheaderrequest) [DeleteFooterRequest](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deletefooterrequest) |

## Related topics

- [Structure of a Google Docs document](https://developers.google.com/workspace/docs/api/concepts/structure)
- [Structural edit rules and behavior](https://developers.google.com/workspace/docs/api/concepts/rules-behavior)
- [Batch requests](https://developers.google.com/workspace/docs/api/how-tos/batch)



# Work with tabs

The Google Docs API lets you access content from any tab in the document.

## What are tabs?

Google Docs features an organizational layer called *tabs* .
Docs allows users to create one or more tabs within a single
document, similar to how there are tabs in Sheets today. Each tab has its own
title and ID (appended in the URL). A tab can also have *child tabs*, which are
tabs that are nested beneath another tab.

## Structural changes to how document content is represented in the Document Resource

In the past, documents did not have a concept of tabs, so the
[`Document`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) Resource directly
contained all of the text contents through the following fields:

- [`document.body`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Body)
- [`document.headers`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Header)
- [`document.footers`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Footer)
- [`document.footnotes`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Footnote)
- [`document.documentStyle`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#DocumentStyle)
- [`document.suggestedDocumentStyleChanges`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#SuggestedDocumentStyle)
- [`document.namedStyles`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#NamedStyles)
- [`document.suggestedNamedStylesChanges`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#SuggestedNamedStyles)
- [`document.lists`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#List)
- [`document.namedRanges`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#NamedRanges)
- [`document.inlineObjects`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#InlineObject)
- [`document.positionedObjects`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#BoPositionedObjectdy)

With the additional structural hierarchy of tabs, these fields no longer
semantically represent the text content from all tabs in the document. The
text-based content is now represented in a different layer. Tab properties and
content in Google Docs are accessible with
[`document.tabs`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Tab), which is
a list of [`Tab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#tab) objects,
each of which contains all of the aforementioned text content fields. The later
sections give a brief overview; the [Tab JSON
representation](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Tab) also
provides more detailed information.

### Access Tab properties

Access tab properties using
[`tab.tabProperties`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#TabProperties),
which includes information such as the ID, title, and positioning of the tab.

### Access text content within a Tab

The actual document content within the tab is exposed as
[`tab.documentTab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#DocumentTab).
All of the aforementioned text content fields are accessible using
`tab.documentTab`. For example, instead of using `document.body`, you should use
`document.tabs[indexOfTab].documentTab.body`.

### Tab hierarchy

Child tabs are represented in the API as a
[`tab.childTabs`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Tab) field on
[`Tab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#tab). Accessing all tabs
in a document requires traversing the "tree" of child tabs. For example,
consider a document that contains a tab hierarchy as follows:

![Tablist UI containing three top-level tabs, some of which have child tabs](https://developers.google.com/static/workspace/docs/api/images/tablist.png)

In order to retrieve the [`Body`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Body)
from *Tab 3.1.2* , you would access
`document.tabs[2].childTabs[0].childTabs[1].documentTab.body`. See the sample
code blocks in the later section, which provides sample code for iterating
across all tabs in a document.

## Changes to methods

With the introduction of tabs, each of the document methods have a few changes
that may require you to update your code.

### documents.get

By default, not all tab contents are returned. Developers should update their
code to access all tabs. The
[`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method
exposes an `includeTabsContent` parameter which enables configuring whether
contents from all tabs are provided in the response.

- If `includeTabsContent` is set to `true`, the [`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) method will return a [`Document`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) Resource with the `document.tabs` field populated. All of the text fields directly on `document` (e.g. `document.body`) will be left as empty.
- If `includeTabsContent` is not provided, then the text fields in the [`Document`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) Resource (e.g. `document.body`) will be populated with content from the first tab only. The `document.tabs` field will be empty and content from other tabs won't be returned.

### documents.create

The [`documents.create`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/create)
method returns a [`Document`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents)
Resource representing the empty document which was created. The returned
[`Document`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents) Resource will
populate the empty document contents in both the document's text content fields
as well as `document.tabs`.

### document.batchUpdate

Each
[`Request`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#request)
includes a way to specify the tabs to apply the update to. By default, if a tab
is not specified, the
[`Request`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#request)
will in most cases be applied to the first tab in the document.
[`ReplaceAllTextRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#replacealltextrequest),
[`DeleteNamedRangeRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#deletenamedrangerequest),
and
[`ReplaceNamedRangeContentRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#replacenamedrangecontentrequest)
are three special requests that will instead default to applying to all tabs.

Refer to the
[`Request`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#request)s
documentation for specifics.

## Changes to internal links

Users can create internal links to tabs, bookmarks, and headings in a document.
With the introduction of the tabs feature, the `link.bookmarkId` and
`link.headingId` fields in the
[`Link`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Link) resource can no
longer represent a bookmark or heading in a particular tab in the document.

Developers should update their code to use `link.bookmark` and `link.heading` in
read and write operations. They expose internal links using
[`BookmarkLink`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#BookmarkLink)
and [`HeadingLink`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#HeadingLink)
objects, each containing the ID of the bookmark or heading and the ID of the tab
it is located in. Additionally, `link.tabId` exposes internal links to tabs.

Link contents of a
[`documents.get`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get) response
can also vary depending on the `includeTabsContent` parameter:

- If `includeTabsContent` is set to `true`, all internal links will be exposed as `link.bookmark` and `link.heading`. The legacy fields will no longer be used.
- If `includeTabsContent` is not provided, then in documents containing a single tab, any internal links to bookmarks or headings within that singular tab continue to be exposed as `link.bookmarkId` and `link.headingId`. In documents containing multiple tabs, internal links will be exposed as `link.bookmark` and `link.heading`.

In
[`document.batchUpdate`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate),
if an internal link is created using one of the legacy fields, the bookmark or
heading will be considered to be from the tab ID specified in the
[`Request`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#request). If
no tab is specified, it will be considered to be from the first tab in the
document.

The [Link JSON
representation](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Link) provides
more detailed information.

## Common usage patterns for tabs

The following code samples describe various ways of interacting with tabs.

### Read tab content from all tabs in the document

Existing code that did this before the tabs feature can be migrated to support
tabs by setting the `includeTabsContent` parameter to `true`, traversing the
tabs tree hierarchy, and calling getter methods off of
[`Tab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#Tab) and
[`DocumentTab`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents#DocumentTab)
instead of [`Document`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents). The
following partial code sample is based off of the snippet at [Extract the text
from a document](https://developers.google.com/workspace/docs/api/samples/extract-text). It shows how to
print all of the text contents from every tab in a document. This tab traversal
code can be adapted for many other use cases which don't care about the actual
structure of the tabs.

### Java

```java
/** Prints all text contents from all tabs in the document. */
static void printAllText(Docs service, String documentId) throws IOException {
  // Fetch the document with all of the tabs populated, including any nested
  // child tabs.
  Document doc =
      service.documents().get(<var>DOCUMENT_ID</var>).setIncludeTabsContent(true).execute();
  List<Tab> allTabs = getAllTabs(doc);

  // Print the content from each tab in the document.
  for (Tab tab: allTabs) {
    // Get the DocumentTab from the generic Tab.
    DocumentTab documentTab = tab.getDocumentTab();
    System.out.println(
        readStructuralElements(documentTab.getBody().getContent()));
  }
}

/**
 * Returns a flat list of all tabs in the document in the order they would
 * appear in the UI (top-down ordering). Includes all child tabs.
 */
private List<Tab> getAllTabs(Document doc) {
  List<Tab> allTabs = new ArrayList<>();
  // Iterate over all tabs and recursively add any child tabs to generate a
  // flat list of Tabs.
  for (Tab tab: doc.getTabs()) {
    addCurrentAndChildTabs(tab, allTabs);
  }
  return allTabs;
}

/**
 * Adds the provided tab to the list of all tabs, and recurses through and
 * adds all child tabs.
 */
private void addCurrentAndChildTabs(Tab tab, List<Tab> allTabs) {
  allTabs.add(tab);
  for (Tab tab: tab.getChildTabs()) {
    addCurrentAndChildTabs(tab, allTabs);
  }
}

/**
 * Recurses through a list of Structural Elements to read a document's text
 * where text may be in nested elements.
 *
 * <p>For a code sample, see
 * <a href="https://developers.google.com/workspace/docs/api/samples/extract-text">Extract
 * the text from a document</a>.
 */
private static String readStructuralElements(List<StructuralElement> elements) {
  ...
}
```

### Read tab content from the first tab in the document

This is similar to reading all tabs.

### Java

```java
/** Prints all text contents from the first tab in the document. */
static void printAllText(Docs service, String documentId) throws IOException {
  // Fetch the document with all of the tabs populated, including any nested
  // child tabs.
  Document doc =
      service.documents().get(<var>DOCUMENT_ID</var>).setIncludeTabsContent(true).execute();
  List<Tab> allTabs = getAllTabs(doc);

  // Print the content from the first tab in the document.
  Tab firstTab = allTabs.get(0);
  // Get the DocumentTab from the generic Tab.
  DocumentTab documentTab = firstTab.getDocumentTab();
  System.out.println(
      readStructuralElements(documentTab.getBody().getContent()));
}
```

### Make a Request to update the first tab

The following partial code sample shows how to target a specific tab in a
[`Request`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#request).
This code is based off of the sample in the
[Insert, delete, and move text](https://developers.google.com/workspace/docs/api/how-tos/move-text#insert-text)
guide.

### Java

```java
/** Inserts text into the first tab of the document. */
static void insertTextInFirstTab(Docs service, String documentId)
    throws IOException {
  // Get the first tab's ID.
  Document doc =
      service.documents().get(<var>DOCUMENT_ID</var>).setIncludeTabsContent(true).execute();
  Tab firstTab = doc.getTabs().get(0);
  String tabId = firstTab.getTabProperties().getTabId();

  List<Request>requests = new ArrayList<>();
  requests.add(new Request().setInsertText(
      new InsertTextRequest().setText(text).setLocation(new Location()
                                                            // Set the tab ID.
                                                            .setTabId(tabId)
                                                            .setIndex(25))));

  BatchUpdateDocumentRequest body =
      new BatchUpdateDocumentRequest().setRequests(requests);
  BatchUpdateDocumentResponse response =
      docsService.documents().batchUpdate(<var>DOCUMENT_ID</var>, body).execute();
}
```




# Output document contents as JSON with Docs API

You can use the following Google Docs API sample to dump the contents of a document
as formatted JSON.

The resulting dump can help you understand the structure of Google Docs
files in general, or help you troubleshoot issues around the structure and
content of a particular document.

> [!NOTE]
> You'll need to have a project set up first, so that you have some credentials to make this work. See the [quickstarts](https://developers.google.com/workspace/docs/api/quickstart/python) for details.

## Source code

### Java

```java
import com.google.api.client.auth.oauth2.Credential;
import com.google.api.client.extensions.java6.auth.oauth2.AuthorizationCodeInstalledApp;
import com.google.api.client.extensions.jetty.auth.oauth2.LocalServerReceiver;
import com.google.api.client.googleapis.auth.oauth2.GoogleAuthorizationCodeFlow;
import com.google.api.client.googleapis.auth.oauth2.GoogleClientSecrets;
import com.google.api.client.googleapis.javanet.GoogleNetHttpTransport;
import com.google.api.client.http.javanet.NetHttpTransport;
import com.google.api.client.json.JsonFactory;
import com.google.api.client.json.jackson2.JacksonFactory;
import com.google.api.client.util.store.FileDataStoreFactory;
import com.google.api.services.docs.v1.Docs;
import com.google.api.services.docs.v1.DocsScopes;
import com.google.api.services.docs.v1.model.Document;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.security.GeneralSecurityException;
import java.util.Collections;
import java.util.List;

public class OutputJSON {
  private static final String APPLICATION_NAME = "Google Docs API Document Contents";
  private static final JsonFactory JSON_FACTORY = JacksonFactory.getDefaultInstance();
  private static final String TOKENS_DIRECTORY_PATH = "tokens";
  private static final String DOCUMENT_ID = "<var>YOUR_DOCUMENT_ID</var>";

  /**
   * Global instance of the scopes required by this sample. If modifying these scopes, delete
   * your previously saved tokens/ folder.
   */
  private static final List<String> SCOPES =
      Collections.singletonList(DocsScopes.DOCUMENTS_READONLY);

  private static final String CREDENTIALS_FILE_PATH = "/credentials.json";

  /**
   * Creates an authorized Credential object.
   *
   * @param HTTP_TRANSPORT The network HTTP Transport.
   * @return An authorized Credential object.
   * @throws IOException If the credentials.json file cannot be found.
   */
  private static Credential getCredentials(final NetHttpTransport HTTP_TRANSPORT)
      throws IOException {
    // Load client secrets.
    InputStream in = OutputJSON.class.getResourceAsStream(CREDENTIALS_FILE_PATH);
    GoogleClientSecrets credentials =
        GoogleClientSecrets.load(JSON_FACTORY, new InputStreamReader(in));

    // Build flow and trigger user authorization request.
    GoogleAuthorizationCodeFlow flow =
        new GoogleAuthorizationCodeFlow.Builder(HTTP_TRANSPORT, JSON_FACTORY, credentials, SCOPES)
            .setDataStoreFactory(new FileDataStoreFactory(new java.io.File(TOKENS_DIRECTORY_PATH)))
            .setAccessType("offline")
            .build();
    LocalServerReceiver receiver = new LocalServerReceiver.Builder().setPort(8888).build();
    return new AuthorizationCodeInstalledApp(flow, receiver).authorize("user");
  }

  public static void main(String... args) throws IOException, GeneralSecurityException {
    // Build a new authorized API client service.
    final NetHttpTransport HTTP_TRANSPORT = GoogleNetHttpTransport.newTrustedTransport();
    Docs docsService =
        new Docs.Builder(HTTP_TRANSPORT, JSON_FACTORY, getCredentials(HTTP_TRANSPORT))
            .setApplicationName(APPLICATION_NAME)
            .build();

    Document response =
        docsService.documents().get(DOCUMENT_ID).setIncludeTabsContent(true).execute();
    Gson gson = new GsonBuilder().setPrettyPrinting().create();
    System.out.println(gson.toJson(response));
  }
}
```

### JavaScript

```
<!DOCTYPE html>
<html>
  <head>
    <title>
      Docs API Extract Body
    </title>
    <meta charset="utf-8"/>
  </head>
  <body>
    <p>
      Docs API Extract Body
    </p>
    <!--Add buttons to initiate auth sequence and sign out-->
    <button id="authorize-button" style="display: none;">Authorize</button>
    <button id="signout-button" style="display: none;">Sign Out</button>
    <pre id="content"></pre>

    <script type="text/javascript">
      // Client ID and API key from the Developer Console
      var CLIENT_ID = '<YOUR_CLIENT_ID>'
      var API_KEY = '<YOUR_API_KEY>';

      // Array of API discovery doc URLs for APIs used by the sample
      var DISCOVERY_DOCS = [
        'https://docs.googleapis.com/$discovery/rest?version=v1'];

      // Authorization scopes required by the API; multiple scopes can be
      // included, separated by spaces.
      var SCOPES = "https://www.googleapis.com/auth/documents.readonly";

      var authorizeButton = document.getElementById('authorize-button');
      var signoutButton = document.getElementById('signout-button');

      /**
       *  On load, called to load the auth2 library and API client library.
       */
      function handleClientLoad() {
        gapi.load('client:auth2', initClient);
      }

      /**
       *  Initializes the API client library and sets up sign-in state
       *  listeners.
       */
      function initClient() {
        gapi.client.init({
        apiKey: API_KEY,
        clientId: CLIENT_ID,
        discoveryDocs: DISCOVERY_DOCS,
        scope: SCOPES
      }).then(function () {
        // Listen for sign-in state changes.
        gapi.auth2.getAuthInstance().isSignedIn.listen(updateSigninStatus);

      // Handle the initial sign-in state.
        updateSigninStatus(gapi.auth2.getAuthInstance().isSignedIn.get());
        authorizeButton.onclick = handleAuthClick;
        signoutButton.onclick = handleSignoutClick;
        });
      }

      /**
       *  Called when the signed in status changes, to update the UI
       *  appropriately. After a sign-in, the API is called.
       */
      function updateSigninStatus(isSignedIn) {
        if (isSignedIn) {
        authorizeButton.style.display = 'none';
        signoutButton.style.display = 'block';
        printDocBody();
        } else {
        authorizeButton.style.display = 'block';
        signoutButton.style.display = 'none';
        }
      }

      /**
       *  Sign in the user upon button click.
       */
      function handleAuthClick(event) {
        gapi.auth2.getAuthInstance().signIn();
      }

      /**
       *  Sign out the user upon button click.
       */
      function handleSignoutClick(event) {
        gapi.auth2.getAuthInstance().signOut();
      }

      /**
       * Append a pre element to the body containing the given message
       * as its text node. Used to display the results of the API call.
       *
       * @param {string} message Text to be placed in pre element.
       */
      function appendPre(message) {
        var pre = document.getElementById('content');
        var textContent = document.createTextNode(message + '\n');
        pre.appendChild(textContent);
      }

      /**
       * Prints the JSON body of a document.
       */
      function printDocBody() {
        gapi.client.docs.documents.get({
        documentId: '<var>DOCUMENT_ID</var>',
        includeTabsContent: true
      }).then(function(response) {
        var doc = response.result;
        appendPre(JSON.stringify(doc.body, null, 4));
      },function(response) {
        appendPre('Error: ' + response.result.error.message);
        });
      }
    </script>
    <script async="" defer="" onload="this.onload=function(){};handleClientLoad()" onreadystatechange="if (this.readyState === 'complete') this.onload()" src="https://apis.google.com/js/api.js"></script>
  </body>
</html>
```

### Python

docs/output-json/output-json.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/docs/output-json/output-json.py)

```python
import json

from apiclient import discovery
from httplib2 import Http
from oauth2client import client, file, tools

# Set doc ID, as found at `https://docs.google.com/document/d/YOUR_DOC_ID/edit`
DOCUMENT_ID = "YOUR_DOC_ID"

# Set the scopes and discovery info
SCOPES = "https://www.googleapis.com/auth/documents.readonly"
DISCOVERY_DOC = "https://docs.googleapis.com/$discovery/rest?version=v1"

# Initialize credentials and instantiate Docs API service
store = file.Storage("token.json")
creds = store.get()
if not creds or creds.invalid:
  flow = client.flow_from_clientsecrets("credentials.json", SCOPES)
  creds = tools.run_flow(flow, store)
service = discovery.build(
    "docs",
    "v1",
    http=creds.authorize(Http()),
    discoveryServiceUrl=DISCOVERY_DOC,
)

# Do a document "get" request and print the results as formatted JSON
result = service.documents().get(documentId=DOCUMENT_ID, includeTabsContent=True).execute()
print(json.dumps(result, indent=4, sort_keys=True))
```

## Example document dump

This section presents a simple document and its equivalent JSON output using
the code above. The source document looks like this:

![](https://developers.google.com/static/workspace/docs/api/images/sampledoc.png)

Running the above code with this document outputs JSON similar to the following:

    {
      "title": "Test mule",
      "revisionId": "np_INheZiecEMA",
      "suggestionsViewMode": "SUGGESTIONS_INLINE",
      "documentId": "18AI89WMd4eI6TFI4VrbmD_srVWJYH2avsXpC_amtLZs",
      "tabs": [{
        "tabProperties": {
          "tabId": "t.0",
          "title": "Tab 1",
          "index": 0
        },
        "documentTab": {
          "body": {
            "content": [{
              "endIndex": 1,
              "sectionBreak": {
                "sectionStyle": {
                  "columnSeparatorStyle": "NONE",
                  "contentDirection": "LEFT_TO_RIGHT",
                  "sectionType": "CONTINUOUS"
                }
              }
            }, {
              "startIndex": 1,
              "endIndex": 75,
              "paragraph": {
                "elements": [{
                  "startIndex": 1,
                  "endIndex": 75,
                  "textRun": {
                    "content": "This is an ordinary paragraph. It is the first paragraph of the document.\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT"
                }
              }
            }, {
              "startIndex": 75,
              "endIndex": 102,
              "paragraph": {
                "elements": [{
                  "startIndex": 75,
                  "endIndex": 102,
                  "textRun": {
                    "content": "Here\u0027s a level one heading\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "headingId": "h.tzrthsds4pvi",
                  "namedStyleType": "HEADING_1",
                  "direction": "LEFT_TO_RIGHT"
                }
              }
            }, {
              "startIndex": 102,
              "endIndex": 219,
              "paragraph": {
                "elements": [{
                  "startIndex": 102,
                  "endIndex": 171,
                  "textRun": {
                    "content": "This is another paragraph. Formatting within this paragraph includes ",
                    "textStyle": {
                    }
                  }
                }, {
                  "startIndex": 171,
                  "endIndex": 190,
                  "textRun": {
                    "content": "these words in bold",
                    "textStyle": {
                      "bold": true
                    }
                  }
                }, {
                  "startIndex": 190,
                  "endIndex": 207,
                  "textRun": {
                    "content": " and these words ",
                    "textStyle": {
                    }
                  }
                }, {
                  "startIndex": 207,
                  "endIndex": 217,
                  "textRun": {
                    "content": "in italics",
                    "textStyle": {
                      "italic": true
                    }
                  }
                }, {
                  "startIndex": 217,
                  "endIndex": 219,
                  "textRun": {
                    "content": ".\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT"
                }
              }
            }, {
              "startIndex": 219,
              "endIndex": 248,
              "paragraph": {
                "elements": [{
                  "startIndex": 219,
                  "endIndex": 248,
                  "textRun": {
                    "content": "This is a bulleted list item\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT",
                  "indentFirstLine": {
                    "magnitude": 18.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 36.0,
                    "unit": "PT"
                  }
                },
                "bullet": {
                  "listId": "kix.v1x94gs10mnc",
                  "textStyle": {
                    "underline": false
                  }
                }
              }
            }, {
              "startIndex": 248,
              "endIndex": 308,
              "paragraph": {
                "elements": [{
                  "startIndex": 248,
                  "endIndex": 308,
                  "textRun": {
                    "content": "And this is another one, which has a numbered list under it\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT",
                  "indentFirstLine": {
                    "magnitude": 18.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 36.0,
                    "unit": "PT"
                  }
                },
                "bullet": {
                  "listId": "kix.v1x94gs10mnc",
                  "textStyle": {
                    "underline": false
                  }
                }
              }
            }, {
              "startIndex": 308,
              "endIndex": 346,
              "paragraph": {
                "elements": [{
                  "startIndex": 308,
                  "endIndex": 346,
                  "textRun": {
                    "content": "This is the first numbered list item.\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT",
                  "indentFirstLine": {
                    "magnitude": 54.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 72.0,
                    "unit": "PT"
                  }
                },
                "bullet": {
                  "listId": "kix.2iaql7na7gwi",
                  "textStyle": {
                    "underline": false
                  }
                }
              }
            }, {
              "startIndex": 346,
              "endIndex": 385,
              "paragraph": {
                "elements": [{
                  "startIndex": 346,
                  "endIndex": 385,
                  "textRun": {
                    "content": "This is the second numbered list item.\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT",
                  "indentFirstLine": {
                    "magnitude": 54.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 72.0,
                    "unit": "PT"
                  }
                },
                "bullet": {
                  "listId": "kix.2iaql7na7gwi",
                  "textStyle": {
                    "underline": false
                  }
                }
              }
            }, {
              "startIndex": 385,
              "endIndex": 460,
              "paragraph": {
                "elements": [{
                  "startIndex": 385,
                  "endIndex": 433,
                  "textRun": {
                    "content": "This is the third numbered list item, which has ",
                    "textStyle": {
                    }
                  }
                }, {
                  "startIndex": 433,
                  "endIndex": 450,
                  "textRun": {
                    "content": "these three words",
                    "textStyle": {
                      "bold": true
                    }
                  }
                }, {
                  "startIndex": 450,
                  "endIndex": 460,
                  "textRun": {
                    "content": " in bold.\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT",
                  "indentFirstLine": {
                    "magnitude": 54.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 72.0,
                    "unit": "PT"
                  }
                },
                "bullet": {
                  "listId": "kix.2iaql7na7gwi",
                  "textStyle": {
                    "underline": false
                  }
                }
              }
            }, {
              "startIndex": 460,
              "endIndex": 496,
              "paragraph": {
                "elements": [{
                  "startIndex": 460,
                  "endIndex": 496,
                  "textRun": {
                    "content": "And a final list item with a bullet\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT",
                  "indentFirstLine": {
                    "magnitude": 18.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 36.0,
                    "unit": "PT"
                  }
                },
                "bullet": {
                  "listId": "kix.ussavk43dhsk",
                  "textStyle": {
                    "underline": false
                  }
                }
              }
            }, {
              "startIndex": 496,
              "endIndex": 497,
              "paragraph": {
                "elements": [{
                  "startIndex": 496,
                  "endIndex": 497,
                  "textRun": {
                    "content": "\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT"
                }
              }
            }, {
              "startIndex": 497,
              "endIndex": 565,
              "table": {
                "rows": 2,
                "columns": 2,
                "tableRows": [{
                  "startIndex": 498,
                  "endIndex": 531,
                  "tableCells": [{
                    "startIndex": 499,
                    "endIndex": 515,
                    "content": [{
                      "startIndex": 500,
                      "endIndex": 515,
                      "paragraph": {
                        "elements": [{
                          "startIndex": 500,
                          "endIndex": 515,
                          "textRun": {
                            "content": "Northwest cell\n",
                            "textStyle": {
                            }
                          }
                        }],
                        "paragraphStyle": {
                          "namedStyleType": "NORMAL_TEXT",
                          "direction": "LEFT_TO_RIGHT"
                        }
                      }
                    }],
                    "tableCellStyle": {
                      "rowSpan": 1,
                      "columnSpan": 1,
                      "backgroundColor": {
                      },
                      "paddingLeft": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingRight": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingTop": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingBottom": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "contentAlignment": "TOP"
                    }
                  }, {
                    "startIndex": 515,
                    "endIndex": 531,
                    "content": [{
                      "startIndex": 516,
                      "endIndex": 531,
                      "paragraph": {
                        "elements": [{
                          "startIndex": 516,
                          "endIndex": 531,
                          "textRun": {
                            "content": "Northeast cell\n",
                            "textStyle": {
                            }
                          }
                        }],
                        "paragraphStyle": {
                          "namedStyleType": "NORMAL_TEXT",
                          "direction": "LEFT_TO_RIGHT"
                        }
                      }
                    }],
                    "tableCellStyle": {
                      "rowSpan": 1,
                      "columnSpan": 1,
                      "backgroundColor": {
                      },
                      "paddingLeft": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingRight": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingTop": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingBottom": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "contentAlignment": "TOP"
                    }
                  }],
                  "tableRowStyle": {
                    "minRowHeight": {
                      "unit": "PT"
                    }
                  }
                }, {
                  "startIndex": 531,
                  "endIndex": 564,
                  "tableCells": [{
                    "startIndex": 532,
                    "endIndex": 548,
                    "content": [{
                      "startIndex": 533,
                      "endIndex": 548,
                      "paragraph": {
                        "elements": [{
                          "startIndex": 533,
                          "endIndex": 548,
                          "textRun": {
                            "content": "Southwest cell\n",
                            "textStyle": {
                            }
                          }
                        }],
                        "paragraphStyle": {
                          "namedStyleType": "NORMAL_TEXT",
                          "direction": "LEFT_TO_RIGHT"
                        }
                      }
                    }],
                    "tableCellStyle": {
                      "rowSpan": 1,
                      "columnSpan": 1,
                      "backgroundColor": {
                      },
                      "paddingLeft": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingRight": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingTop": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingBottom": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "contentAlignment": "TOP"
                    }
                  }, {
                    "startIndex": 548,
                    "endIndex": 564,
                    "content": [{
                      "startIndex": 549,
                      "endIndex": 564,
                      "paragraph": {
                        "elements": [{
                          "startIndex": 549,
                          "endIndex": 564,
                          "textRun": {
                            "content": "Southeast cell\n",
                            "textStyle": {
                            }
                          }
                        }],
                        "paragraphStyle": {
                          "namedStyleType": "NORMAL_TEXT",
                          "direction": "LEFT_TO_RIGHT"
                        }
                      }
                    }],
                    "tableCellStyle": {
                      "rowSpan": 1,
                      "columnSpan": 1,
                      "backgroundColor": {
                      },
                      "paddingLeft": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingRight": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingTop": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "paddingBottom": {
                        "magnitude": 5.0,
                        "unit": "PT"
                      },
                      "contentAlignment": "TOP"
                    }
                  }],
                  "tableRowStyle": {
                    "minRowHeight": {
                      "unit": "PT"
                    }
                  }
                }],
                "tableStyle": {
                  "tableColumnProperties": [{
                    "widthType": "EVENLY_DISTRIBUTED"
                  }, {
                    "widthType": "EVENLY_DISTRIBUTED"
                  }],
                  "contentDirection": "LEFT_TO_RIGHT"
                }
              }
            }, {
              "startIndex": 565,
              "endIndex": 589,
              "paragraph": {
                "elements": [{
                  "startIndex": 565,
                  "endIndex": 589,
                  "textRun": {
                    "content": "And a level two heading\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "headingId": "h.dp3ko4q27ips",
                  "namedStyleType": "HEADING_2",
                  "direction": "LEFT_TO_RIGHT"
                }
              }
            }, {
              "startIndex": 589,
              "endIndex": 649,
              "paragraph": {
                "elements": [{
                  "startIndex": 589,
                  "endIndex": 649,
                  "textRun": {
                    "content": "And this is a paragraph that follows the level two heading.\n",
                    "textStyle": {
                    }
                  }
                }],
                "paragraphStyle": {
                  "namedStyleType": "NORMAL_TEXT",
                  "direction": "LEFT_TO_RIGHT"
                }
              }
            }]
          },
          "documentStyle": {
            "background": {
              "color": {
              }
            },
            "pageNumberStart": 1,
            "marginTop": {
              "magnitude": 72.0,
              "unit": "PT"
            },
            "marginBottom": {
              "magnitude": 72.0,
              "unit": "PT"
            },
            "marginRight": {
              "magnitude": 72.0,
              "unit": "PT"
            },
            "marginLeft": {
              "magnitude": 72.0,
              "unit": "PT"
            },
            "pageSize": {
              "height": {
                "magnitude": 792.0,
                "unit": "PT"
              },
              "width": {
                "magnitude": 612.0,
                "unit": "PT"
              }
            },
            "marginHeader": {
              "magnitude": 36.0,
              "unit": "PT"
            },
            "marginFooter": {
              "magnitude": 36.0,
              "unit": "PT"
            },
            "useCustomHeaderFooterMargins": true
          },
          "namedStyles": {
            "styles": [{
              "namedStyleType": "NORMAL_TEXT",
              "textStyle": {
                "bold": false,
                "italic": false,
                "underline": false,
                "strikethrough": false,
                "smallCaps": false,
                "backgroundColor": {
                },
                "foregroundColor": {
                  "color": {
                    "rgbColor": {
                    }
                  }
                },
                "fontSize": {
                  "magnitude": 11.0,
                  "unit": "PT"
                },
                "weightedFontFamily": {
                  "fontFamily": "Arial",
                  "weight": 400
                },
                "baselineOffset": "NONE"
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "alignment": "START",
                "lineSpacing": 115.0,
                "direction": "LEFT_TO_RIGHT",
                "spacingMode": "COLLAPSE_LISTS",
                "spaceAbove": {
                  "unit": "PT"
                },
                "spaceBelow": {
                  "unit": "PT"
                },
                "borderBetween": {
                  "color": {
                  },
                  "width": {
                    "unit": "PT"
                  },
                  "padding": {
                    "unit": "PT"
                  },
                  "dashStyle": "SOLID"
                },
                "borderTop": {
                  "color": {
                  },
                  "width": {
                    "unit": "PT"
                  },
                  "padding": {
                    "unit": "PT"
                  },
                  "dashStyle": "SOLID"
                },
                "borderBottom": {
                  "color": {
                  },
                  "width": {
                    "unit": "PT"
                  },
                  "padding": {
                    "unit": "PT"
                  },
                  "dashStyle": "SOLID"
                },
                "borderLeft": {
                  "color": {
                  },
                  "width": {
                    "unit": "PT"
                  },
                  "padding": {
                    "unit": "PT"
                  },
                  "dashStyle": "SOLID"
                },
                "borderRight": {
                  "color": {
                  },
                  "width": {
                    "unit": "PT"
                  },
                  "padding": {
                    "unit": "PT"
                  },
                  "dashStyle": "SOLID"
                },
                "indentFirstLine": {
                  "unit": "PT"
                },
                "indentStart": {
                  "unit": "PT"
                },
                "indentEnd": {
                  "unit": "PT"
                },
                "keepLinesTogether": false,
                "keepWithNext": false,
                "avoidWidowAndOrphan": true,
                "shading": {
                  "backgroundColor": {
                  }
                },
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "HEADING_1",
              "textStyle": {
                "fontSize": {
                  "magnitude": 20.0,
                  "unit": "PT"
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "magnitude": 20.0,
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 6.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "HEADING_2",
              "textStyle": {
                "bold": false,
                "fontSize": {
                  "magnitude": 16.0,
                  "unit": "PT"
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "magnitude": 18.0,
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 6.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "HEADING_3",
              "textStyle": {
                "bold": false,
                "foregroundColor": {
                  "color": {
                    "rgbColor": {
                      "red": 0.2627451,
                      "green": 0.2627451,
                      "blue": 0.2627451
                    }
                  }
                },
                "fontSize": {
                  "magnitude": 14.0,
                  "unit": "PT"
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "magnitude": 16.0,
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 4.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "HEADING_4",
              "textStyle": {
                "foregroundColor": {
                  "color": {
                    "rgbColor": {
                      "red": 0.4,
                      "green": 0.4,
                      "blue": 0.4
                    }
                  }
                },
                "fontSize": {
                  "magnitude": 12.0,
                  "unit": "PT"
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "magnitude": 14.0,
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 4.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "HEADING_5",
              "textStyle": {
                "foregroundColor": {
                  "color": {
                    "rgbColor": {
                      "red": 0.4,
                      "green": 0.4,
                      "blue": 0.4
                    }
                  }
                },
                "fontSize": {
                  "magnitude": 11.0,
                  "unit": "PT"
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "magnitude": 12.0,
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 4.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "HEADING_6",
              "textStyle": {
                "italic": true,
                "foregroundColor": {
                  "color": {
                    "rgbColor": {
                      "red": 0.4,
                      "green": 0.4,
                      "blue": 0.4
                    }
                  }
                },
                "fontSize": {
                  "magnitude": 11.0,
                  "unit": "PT"
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "magnitude": 12.0,
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 4.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "TITLE",
              "textStyle": {
                "fontSize": {
                  "magnitude": 26.0,
                  "unit": "PT"
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 3.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }, {
              "namedStyleType": "SUBTITLE",
              "textStyle": {
                "italic": false,
                "foregroundColor": {
                  "color": {
                    "rgbColor": {
                      "red": 0.4,
                      "green": 0.4,
                      "blue": 0.4
                    }
                  }
                },
                "fontSize": {
                  "magnitude": 15.0,
                  "unit": "PT"
                },
                "weightedFontFamily": {
                  "fontFamily": "Arial",
                  "weight": 400
                }
              },
              "paragraphStyle": {
                "namedStyleType": "NORMAL_TEXT",
                "direction": "LEFT_TO_RIGHT",
                "spaceAbove": {
                  "unit": "PT"
                },
                "spaceBelow": {
                  "magnitude": 16.0,
                  "unit": "PT"
                },
                "keepLinesTogether": true,
                "keepWithNext": true,
                "pageBreakBefore": false
              }
            }]
          },
          "lists": {
            "kix.2iaql7na7gwi": {
              "listProperties": {
                "nestingLevels": [{
                  "bulletAlignment": "START",
                  "glyphType": "ALPHA",
                  "glyphFormat": "%0.",
                  "indentFirstLine": {
                    "magnitude": 54.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 72.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "END",
                  "glyphType": "ROMAN",
                  "glyphFormat": "%1.",
                  "indentFirstLine": {
                    "magnitude": 90.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 108.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphType": "DECIMAL",
                  "glyphFormat": "%2.",
                  "indentFirstLine": {
                    "magnitude": 126.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 144.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphType": "ALPHA",
                  "glyphFormat": "%3.",
                  "indentFirstLine": {
                    "magnitude": 162.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 180.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "END",
                  "glyphType": "ROMAN",
                  "glyphFormat": "%4.",
                  "indentFirstLine": {
                    "magnitude": 198.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 216.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphType": "DECIMAL",
                  "glyphFormat": "%5.",
                  "indentFirstLine": {
                    "magnitude": 234.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 252.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphType": "ALPHA",
                  "glyphFormat": "%6.",
                  "indentFirstLine": {
                    "magnitude": 270.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 288.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "END",
                  "glyphType": "ROMAN",
                  "glyphFormat": "%7.",
                  "indentFirstLine": {
                    "magnitude": 306.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 324.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphType": "DECIMAL",
                  "glyphFormat": "%8.",
                  "indentFirstLine": {
                    "magnitude": 342.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 360.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }]
              }
            },
            "kix.ussavk43dhsk": {
              "listProperties": {
                "nestingLevels": [{
                  "bulletAlignment": "START",
                  "glyphSymbol": "●",
                  "glyphFormat": "%0",
                  "indentFirstLine": {
                    "magnitude": 18.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 36.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "○",
                  "glyphFormat": "%1",
                  "indentFirstLine": {
                    "magnitude": 54.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 72.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "■",
                  "glyphFormat": "%2",
                  "indentFirstLine": {
                    "magnitude": 90.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 108.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "●",
                  "glyphFormat": "%3",
                  "indentFirstLine": {
                    "magnitude": 126.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 144.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "○",
                  "glyphFormat": "%4",
                  "indentFirstLine": {
                    "magnitude": 162.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 180.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "■",
                  "glyphFormat": "%5",
                  "indentFirstLine": {
                    "magnitude": 198.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 216.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "●",
                  "glyphFormat": "%6",
                  "indentFirstLine": {
                    "magnitude": 234.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 252.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "○",
                  "glyphFormat": "%7",
                  "indentFirstLine": {
                    "magnitude": 270.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 288.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "■",
                  "glyphFormat": "%8",
                  "indentFirstLine": {
                    "magnitude": 306.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 324.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }]
              }
            },
            "kix.v1x94gs10mnc": {
              "listProperties": {
                "nestingLevels": [{
                  "bulletAlignment": "START",
                  "glyphSymbol": "●",
                  "glyphFormat": "%0",
                  "indentFirstLine": {
                    "magnitude": 18.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 36.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "○",
                  "glyphFormat": "%1",
                  "indentFirstLine": {
                    "magnitude": 54.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 72.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "■",
                  "glyphFormat": "%2",
                  "indentFirstLine": {
                    "magnitude": 90.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 108.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "●",
                  "glyphFormat": "%3",
                  "indentFirstLine": {
                    "magnitude": 126.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 144.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "○",
                  "glyphFormat": "%4",
                  "indentFirstLine": {
                    "magnitude": 162.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 180.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "■",
                  "glyphFormat": "%5",
                  "indentFirstLine": {
                    "magnitude": 198.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 216.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "●",
                  "glyphFormat": "%6",
                  "indentFirstLine": {
                    "magnitude": 234.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 252.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "○",
                  "glyphFormat": "%7",
                  "indentFirstLine": {
                    "magnitude": 270.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 288.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }, {
                  "bulletAlignment": "START",
                  "glyphSymbol": "■",
                  "glyphFormat": "%8",
                  "indentFirstLine": {
                    "magnitude": 306.0,
                    "unit": "PT"
                  },
                  "indentStart": {
                    "magnitude": 324.0,
                    "unit": "PT"
                  },
                  "textStyle": {
                    "underline": false
                  },
                  "startNumber": 1
                }]
              }
            }
          }
        }
      }]
    }