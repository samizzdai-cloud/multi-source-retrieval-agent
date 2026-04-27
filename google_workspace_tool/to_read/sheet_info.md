# Google Sheets API Overview

The Google Sheets API is a RESTful interface that lets you read and modify a
spreadsheet's data. The Sheets API lets you:

- Create spreadsheets
- Read and write spreadsheet cell values
- Update spreadsheet formatting
- Manage Connected Sheets

The following is a list of common terms used in the Sheets API:

*Spreadsheet*

:   The primary object in Google Sheets. It can contain multiple `Sheets`, each
    with structured information contained in `Cells`. The
    [`spreadsheets`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets)
    resource represents a spreadsheet. It contains a unique `spreadsheetId`
    value.

    *Spreadsheet ID*
    :   The unique identifier for a spreadsheet. It's a particular string
        containing letters, numbers, and some special characters that reference a
        spreadsheet and it can be derived from the spreadsheet's URL. Spreadsheet
        IDs are stable, even if the spreadsheet name changes.

        https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit?gid=SHEET_ID#gid=SHEET_ID

*Sheet*

:   A page or tab within a spreadsheet. The
    [`Sheets`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets) resource
    represents a sheet. It contains a unique numeric `sheetId` value and sheet
    `title` as part of the
    [`SheetProperties`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#sheetproperties)
    object.

    *Sheet ID*
    :   The unique identifier for a specific sheet within a spreadsheet. It's a
        particular integer containing numbers that reference a sheet and it can be
        derived from the spreadsheet's URL. Sheet IDs are stable, even if the sheet
        name changes. For an example, see [Spreadsheet ID](https://developers.google.com/workspace/sheets/api/guides/concepts#spreadsheet-id).

*Cell*

:   An individual field of text or data within a sheet. Cells are arranged in rows
    and columns, and can be grouped as a range of cells. The
    [`Cells`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/cells)
    resource represents each cell, but it doesn't have a unique ID value.
    Instead, row and column coordinates identify the cells.

    *A1 notation*
    :   A syntax used to define a cell or range of cells with a string that
        contains the sheet name plus the starting and ending cell coordinates using
        column letters and row numbers. This method is the most common and useful
        when referencing an absolute range of cells.
    - `Sheet1!A1:B2` refers to all the cells in the first two rows and columns of Sheet1.
    - `Sheet1!A:A` refers to all the cells in the first column of Sheet1.
    - `Sheet1!1:2` refers to all the cells in the first two rows of Sheet1.
    - `Sheet1!A5:A` refers to all the cells of the first column of Sheet 1, from row 5 onward.
    - `A1:B2` refers to all the cells in the first two rows and columns of the first visible sheet.
    - `Sheet1` refers to all the cells in Sheet1.
    - `'Jon's_Data'!A1:D5` refers to all the cells in the first five rows and four columns of a sheet named "Jon's_Data."
    - `'My Custom Sheet'!A:A` refers to all the cells in the first column of a sheet named "My Custom Sheet."
    - `'My Custom Sheet'` refers to all the cells in "My Custom Sheet".

    Single quotes are required for sheet names with spaces or special characters.

    **Tip** : Where possible, use distinct names for objects in your spreadsheets. For example, A1 (without quotes) refers to cell A1 in the first visible sheet. `'A1'` refers to a sheet with the name A1. Similarly, Sheet1 refers to a sheet with the name Sheet1. However, if there's a named range titled "Sheet1", then Sheet1 refers to the named range and `'Sheet1'` refers to the sheet.

    *R1C1 notation*
    :   A syntax used to define a cell or range of cells with a string that
        contains the sheet name plus the starting and ending cell coordinates using
        row numbers and column numbers. This method is less common than A1 notation,
        but can be useful when referencing a range of cells relative to a given
        cell's position.
    - `Sheet1!R1C1:R2C2` refers to the first two cells in the top two rows of Sheet1.
    - `R1C1:R2C2` refers to the first two cells in the top two rows of the first visible sheet.
    - `Sheet1!R[3]C[1]` refers to the cell that is three rows below and one column to the right of the current cell.

    *Named range*
    :   A defined cell or range of cells with a custom name to simplify references
        throughout an application. A
        [`FilterView`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#filterview)
        resource represents a named range.

    *Protected range*
    :   A defined cell or range of cells that cannot be modified. A
        [`ProtectedRange`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#protectedrange)
        resource represents a protected range.

## Related topics

- To learn about developing with Google Workspace APIs, including handling
  authentication and authorization, refer to [Develop on
  Google Workspace](https://developers.google.com/workspace/guides/get-started).

- To learn how to configure and run a Sheets API app, try the
  [JavaScript quickstart](https://developers.google.com/workspace/sheets/api/quickstart/js).


  # Read &amp; write cell values

Spreadsheets can have multiple sheets, with each sheet having any number of rows
or columns. A [cell](https://developers.google.com/workspace/sheets/api/guides/concepts#cell) is a location
at the intersection of a particular row and column, and might contain a data
value. The Google Sheets API provides the
[`spreadsheets.values`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values)
resource to enable the reading and writing of values.

This page describes the basics of using the `spreadsheets.values` resource. If
you need to insert rows or update the formatting and other properties in a
sheet, you must use the
[`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method described in [Update
spreadsheets](https://developers.google.com/workspace/sheets/api/guides/batchupdate).

## Methods

The
[`spreadsheets.values`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets)
resource provides the following methods for reading and writing values, each for
a specific task:

| Range Access | Reading | Writing |
|---|---|---|
| Single range | [`spreadsheets.values.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get) | [`spreadsheets.values.update`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update) |
| Multiple ranges | [`spreadsheets.values.batchGet`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet) | [`spreadsheets.values.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate) |
| Appending |   | [`spreadsheets.values.append`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append) |

In general, it's a good idea to combine multiple reads or updates with the
`batchGet` and `batchUpdate` methods (respectively), as this improves
efficiency.

You can find examples of each of these methods on the [Basic
reading](https://developers.google.com/workspace/sheets/api/samples/reading) and [Basic
writing](https://developers.google.com/workspace/sheets/api/samples/writing) samples pages. To see all
samples, refer to the [samples overview page](https://developers.google.com/workspace/sheets/api/samples).

## Read

To read data values from a sheet, you need the spreadsheet ID and the A1
notation for the range. Specifying the range without the sheet ID (`A1:B2`)
means that the request executes on the first sheet in the spreadsheet. For more
information about spreadsheet IDs and A1 notation, see [Google Sheets API
Overview](https://developers.google.com/workspace/sheets/api/guides/concepts).

Several optional query parameters control the format of the output:

| Format Parameter | Default Value |
|---|---|
| [`majorDimension`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values#dimension) | ROWS |
| [`valueRenderOption`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueRenderOption) | FORMATTED_VALUE |
| [`dateTimeRenderOption`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DateTimeRenderOption) | SERIAL_NUMBER |

Note that you should only use `dateTimeRenderOption` if the `valueRenderOption`
isn't `FORMATTED_VALUE`.

There's no explicit limit to the amount of data returned. Errors return no data.
Empty trailing rows and columns are omitted.

The singular and batch get methods are described below. For samples of basic
read operations, see [Basic reading](https://developers.google.com/workspace/sheets/api/samples/reading).

### Read a single range

To read a single range of values from a spreadsheet, use a
[`spreadsheets.values.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get)
request:

### Apps Script

sheets/api/spreadsheet_snippets.gs [View on GitHub](https://github.com/googleworkspace/apps-script-samples/blob/main/sheets/api/spreadsheet_snippets.gs)


### Python

sheets/snippets/sheets_get_values.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_get_values.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def get_values(spreadsheet_id, range_name):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    rows = result.get("values", [])
    print(f"{len(rows)} rows retrieved")
    return result
  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: spreadsheet_id, and range_name
  get_values("1CM29gwKIzeXsAppeNwrc8lbYaVMmUclprLuLYuHog4k", "A1:C2")
```



The response to this request is returned as a
[`ValueRange`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values#resource-valuerange)
object.

### Read multiple ranges

To read multiple, discontinuous ranges of values from a spreadsheet, use a
[`spreadsheets.values.batchGet`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet)
request that lets you specify several ranges to retrieve:

### Apps Script

sheets/api/spreadsheet_snippets.gs [View on GitHub](https://github.com/googleworkspace/apps-script-samples/blob/main/sheets/api/spreadsheet_snippets.gs)


### Python

sheets/snippets/sheets_batch_get_values.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_batch_get_values.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def batch_get_values(spreadsheet_id, _range_names):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)
    range_names = [
        # Range names ...
    ]
    result = (
        service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=spreadsheet_id, ranges=range_names)
        .execute()
    )
    ranges = result.get("valueRanges", [])
    print(f"{len(ranges)} ranges retrieved")
    return result
  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: spreadsheet_id, and range_name

  batch_get_values("1CM29gwKIzeXsAppeNwrc8lbYaVMmUclprLuLYuHog4k", "A1:C2")
```


The response to this request is returned as a
[`BatchGetValuesResponse`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet#response-body)
object that contains the `spreadsheetId` and a list of
[`ValueRange`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values#resource-valuerange)
objects.

## Write

To write to a sheet, you need the spreadsheet ID, the range of cells in A1
notation, and the data you wish to write within an appropriate request body
object. For more information about spreadsheet IDs and A1 notation, see [Google Sheets API Overview](https://developers.google.com/workspace/sheets/api/guides/concepts).

Updates require a valid
[`ValueInputOption`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueInputOption) parameter.
For singular updates, this is a required query parameter. For batch updates,
this parameter is required in the request body. The `ValueInputOption` controls
how input data should be interpreted and whether input strings are parsed or
not, as described in the following table:

| `ValueInputOption` | Description |
|---|---|
| `RAW` | The input is not parsed and is inserted as a string. For example, the input "=1+2" places the string, not the formula, "=1+2" in the cell. (Non-string values like booleans or numbers are always handled as `RAW`.) |
| `USER_ENTERED` | The input is parsed exactly as if it were entered into the Sheets UI. For example, "Mar 1 2016" becomes a date, and "=1+2" becomes a formula. Formats can also be inferred, so "$100.15" becomes a number with currency formatting. |

The singular and batch update methods are described below. For samples of basic
write operations, see [Basic writing](https://developers.google.com/workspace/sheets/api/samples/writing).

### Write to a single range

To write data to a single range, use a
[`spreadsheets.values.update`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update)
request:

### Apps Script

sheets/api/spreadsheet_snippets.gs [View on GitHub](https://github.com/googleworkspace/apps-script-samples/blob/main/sheets/api/spreadsheet_snippets.gs)



### Python

sheets/snippets/sheets_update_values.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_update_values.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def update_values(spreadsheet_id, range_name, value_input_option, _values):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)
    values = [
        [
            # Cell values ...
        ],
        # Additional rows ...
    ]
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=value_input_option,
            body=body,
        )
        .execute()
    )
    print(f"{result.get('updatedCells')} cells updated.")
    return result
  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: spreadsheet_id,  range_name, value_input_option and  _values
  update_values(
      "1CM29gwKIzeXsAppeNwrc8lbYaVMmUclprLuLYuHog4k",
      "A1:C2",
      "USER_ENTERED",
      [["A", "B"], ["C", "D"]],
  )
```

# Create and manage spreadsheets

This document explains how to create and manage spreadsheets in Google Sheets
using the Google Sheets API.

## Create a spreadsheet

To create a file in Sheets, use the
[`create`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/create) method on the
[`spreadsheets`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets) resource
with no parameters.

When you create the file, the method returns a `spreadsheets` resource. The
returned resource contains a `spreadsheetId`, `properties`, a list of `sheets`,
and a `spreadsheetUrl`.

The following code sample shows how to create a blank spreadsheet with a
specified title.

### Apps Script

sheets/api/spreadsheet_snippets.gs [View on GitHub](https://github.com/googleworkspace/apps-script-samples/blob/main/sheets/api/spreadsheet_snippets.gs)

```javascript
/**
 * Creates a new sheet using the sheets advanced services
 * @param {string} title the name of the sheet to be created
 * @returns {string} the spreadsheet ID
 */
Snippets.prototype.create = (title) => {
  // This code uses the Sheets Advanced Service, but for most use cases
  // the built-in method SpreadsheetApp.create() is more appropriate.
  try {
    const sheet = Sheets.newSpreadsheet();
    sheet.properties = Sheets.newSpreadsheetProperties();
    sheet.properties.title = title;
    const spreadsheet = Sheets.Spreadsheets.create(sheet);

    return spreadsheet.spreadsheetId;
  } catch (err) {
    // TODO (developer) - Handle exception
    console.log("Failed with error %s", err.message);
  }
};
```


### Python

sheets/snippets/sheets_create.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_create.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def create(title):
  """
  Creates the Sheet the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)
    spreadsheet = {"properties": {"title": title}}
    spreadsheet = (
        service.spreadsheets()
        .create(body=spreadsheet, fields="spreadsheetId")
        .execute()
    )
    print(f"Spreadsheet ID: {(spreadsheet.get('spreadsheetId'))}")
    return spreadsheet.get("spreadsheetId")
  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: title
  create("mysheet1")
```

### Organize spreadsheets in Google Drive folders

By default, the created spreadsheet is saved to the user's root folder on
Google Drive.

If you want to save a spreadsheet to a specified Drive folder,
use these methods:

- After the spreadsheet file is created, move it to a specific folder using
  the [`update`](https://developers.google.com/drive/api/v3/reference/files/update) method on the
  [`files`](https://developers.google.com/drive/api/v3/reference/files) resource of Google Drive API. For more
  information, see [Move files between
  folders](https://developers.google.com/workspace/drive/api/guides/folder#move-files).

- Add a blank spreadsheet to a folder using the
  [`create`](https://developers.google.com/drive/api/v3/reference/files/create) method on the `files`
  resource of Drive API, specifying
  `application/vnd.google-apps.spreadsheet` as the
  [`mimeType`](https://developers.google.com/drive/api/v3/reference/files#File.FIELDS.mime_type). For more
  information on creating files, see [Create a file in a specific
  folder](https://developers.google.com/workspace/drive/api/guides/folder#create-file). For more
  information on MIME types, see [Google Workspace and Google Drive
  supported MIME types](https://developers.google.com/workspace/drive/api/guides/mime-types).

For either alternative, you'll need to add the appropriate [Drive
API
scopes](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create#authorization-scopes)
to authorize the call.

If your app is using a Service Account, the Service Account owns the created
spreadsheet. This file then resides in the Service Account's dedicated
Drive storage. Files don't appear within other
Drive storage accounts unless explicitly shared. For more
information, see [File
ownership](https://developers.google.com/workspace/drive/api/guides/create-file#file-ownership).

To move or create a file within a shared drive folder, refer to [Implement
shared drive support](https://developers.google.com/workspace/drive/api/guides/enable-shareddrives).

To learn more about cell and row limits in Google Sheets, see [Files you can
store in Google Drive](https://support.google.com/drive/answer/37603).

## Get a spreadsheet

To get a spreadsheet, use the
[`get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get) method on the
`spreadsheets` resource with the `spreadsheetId` path parameter.

The method returns the file as an instance of a `spreadsheets` resource. By
default, data within the spreadsheet isn't returned. The returned resource
contains the structure and metadata of the spreadsheet, including the
spreadsheet properties (such as title, locale, and time zone) and some detailed
sheet information (such as formatting and protected ranges).

To include data within a `spreadsheets` resource, use these two methods:

- Specify a [field mask](https://developers.google.com/workspace/sheets/api/guides/field-masks) listing
  your selected fields by setting the `fields` [system
  parameter](https://cloud.google.com/apis/docs/system-parameters#definitions).

- Set the boolean [`includeGridData`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get#body.QUERY_PARAMETERS.include_grid_data)
  query parameter to `true`. If a field mask is set, the `includeGridData`
  parameter is ignored.

When you're working with large spreadsheets, we recommend that you query for
only the specific spreadsheet fields that you need. The `get` method returns all
the data associated with the spreadsheet, so general queries for large
spreadsheets can be slow. For example, to read the number 100 from a cell,
`spreadsheets.get` returns the cell value plus metadata (such as font name,
size, etc.) resulting in large JSON payloads that are slow to parse. In
comparison, a similar call to `values.get` returns only the specific cell value
resulting in a much lighter, faster response.

For more information on the `spreadsheets.values` resource, including
[`spreadsheets.values.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get)
and
[`spreadsheets.values.batchGet`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet),
see the following documents:

- [Read and write cell values](https://developers.google.com/workspace/sheets/api/guides/values)
- [Basic reading](https://developers.google.com/workspace/sheets/api/samples/reading)

## List spreadsheets

The Sheets API doesn't offer a method to list spreadsheets for
authenticated users.

To retrieve a list of spreadsheets, you can use Drive API's
[`list`](https://developers.google.com/workspace/drive/v3/reference/files/list) method on the
[`files`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files) resource, specifying
`application/vnd.google-apps.spreadsheet` as the
[`mimeType`](https://developers.google.com/drive/api/v3/reference/files#File.FIELDS.mime_type):

### HTTP

    GET https://www.googleapis.com/drive/v3/files?q=mimeType='application/vnd.google-apps.spreadsheet'

### cURL

    curl -X GET "https://www.googleapis.com/drive/v3/files?q=mimeType='application/vnd.google-apps.spreadsheet'" \
     -H "Authorization: Bearer ACCESS_TOKEN" \
     -H "Accept: application/json"

Replace <var translate="no">ACCESS_TOKEN</var> with the access token that grants
access to the API.

Using the `files.list` method to list a user's spreadsheets requires a
[restricted](https://developers.google.com/workspace/drive/api/guides/api-specific-auth) Drive API
scope.

## Related topics

Here are a few next steps you might try:

- To update a spreadsheet, see [Update
  spreadsheets](https://developers.google.com/workspace/sheets/api/guides/batchupdate).

- To work with smart chips, see [Smart
  chips](https://developers.google.com/workspace/sheets/api/guides/chips).

- To work with developer metadata, see [Read, write, and search
  metadata](https://developers.google.com/workspace/sheets/api/guides/metadata).





The body of the update request must be a
[`ValueRange`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values#resource-valuerange)
object, though the only required field is `values`. If `range` is specified, it
must match the range in the URL. In the `ValueRange`, you can optionally specify
its
[`majorDimension`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values#dimension).
By default, `ROWS` is used. If `COLUMNS` is specified, each inner array is
written to a column instead of a row.

When updating, values with no data are skipped. To clear data, use an empty
string ("").

> [!NOTE]
> A `range` parameter can specify multiple cells (for example, A1:D5) or a single cell (for example, A1). If it specifies multiple cells, the input data must be within that range. If it specifies a single cell, the input data starts at that coordinate and can extend any number of rows or columns.

### Write multiple ranges

If you want to write multiple discontinuous ranges, you can use a
[`spreadsheets.values.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)
request:

### Apps Script

sheets/api/spreadsheet_snippets.gs [View on GitHub](https://github.com/googleworkspace/apps-script-samples/blob/main/sheets/api/spreadsheet_snippets.gs)



### Python

sheets/snippets/sheets_batch_update_values.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_batch_update_values.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def batch_update_values(
    spreadsheet_id, range_name, value_input_option, _values
):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)

    values = [
        [
            # Cell values ...
        ],
        # Additional rows
    ]
    data = [
        {"range": range_name, "values": values},
        # Additional ranges to update ...
    ]
    body = {"valueInputOption": value_input_option, "data": data}
    result = (
        service.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    print(f"{(result.get('totalUpdatedCells'))} cells updated.")
    return result
  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: spreadsheet_id, range_name value_input_option and _values)
  batch_update_values(
      "1CM29gwKIzeXsAppeNwrc8lbYaVMmUclprLuLYuHog4k",
      "A1:C2",
      "USER_ENTERED",
      [["F", "B"], ["C", "D"]],
  )
```


### Append values

To append data after a table of data in a sheet, use a
[`spreadsheets.values.append`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append)
request:

### Apps Script

sheets/api/spreadsheet_snippets.gs [View on GitHub](https://github.com/googleworkspace/apps-script-samples/blob/main/sheets/api/spreadsheet_snippets.gs)




### Python

sheets/snippets/sheets_append_values.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_append_values.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def append_values(spreadsheet_id, range_name, value_input_option, _values):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)

    values = [
        [
            # Cell values ...
        ],
        # Additional rows ...
    ]
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=value_input_option,
            body=body,
        )
        .execute()
    )
    print(f"{(result.get('updates').get('updatedCells'))} cells appended.")
    return result

  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: spreadsheet_id, range_name value_input_option and _values)
  append_values(
      "1CM29gwKIzeXsAppeNwrc8lbYaVMmUclprLuLYuHog4k",
      "A1:C2",
      "USER_ENTERED",
      [["F", "B"], ["C", "D"]],
  )
```


# Read, write, and search metadata

The metadata feature lets you associate metadata with various entities and
locations in a spreadsheet. You can then query this metadata and use it to find
the objects with which it's associated.

You can associate metadata with rows, columns, sheets, or a spreadsheet.

## About metadata

The following describes some key aspects of metadata that you should consider
when working with the Sheets API:

1. **Metadata as tags** : One use of developer metadata is a *tag* that names a
   location in the spreadsheet using only a key and a location. For example,
   you can associate `headerRow` with a particular row or `totals` with a
   particular column within a sheet. Tags can be used to semantically bind
   portions of a spreadsheet to fields in a third-party tool or database, so
   changes to the spreadsheet won't break your app.

2. **Metadata as properties** : Metadata created by specifying a key, location,
   and a *value* acts as a key-value pair associated with that location in a
   sheet. For example, you can associate:

   - `formResponseId = resp123` with a row
   - `lastUpdated = 1477369882` with a column

   This lets you store and access custom named properties associated with
   particular areas or data in a spreadsheet.
3. **Project versus document visible metadata** : To prevent one developer
   project from interfering with another's metadata, there are two metadata
   [`visibility`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata#DeveloperMetadata.FIELDS.visibility)
   settings: `project` and `document`. Using the Sheets API,
   `project` metadata is only visible and accessible from the Google Cloud project
   that created it. The `document` metadata is accessible from any
   Google Cloud project with access to the document.

   Queries that don't explicitly specify a `visibility` return matching
   `document` metadata and matching `project` metadata for the Google Cloud project
   making the request.

   > [!WARNING]
   > Don't use project-visible developer metadata as a security mechanism or to store secrets. The metadata might be exposed to users with view access to the document.

4. **Uniqueness** : Metadata keys don't have to be unique, but the
   [`metadataId`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata#DeveloperMetadata.FIELDS.metadata_id)
   must be distinct. If you create metadata and leave its ID field unspecified,
   the API assigns one. This ID can be used to identify the metadata, while
   keys and other attributes can be used to identify sets of metadata.

   > [!NOTE]
   > Although you can specify your own \`metadataId\` when creating metadata, we recommend you allow the Sheets API to assign one for you.

5. **Return metadata through API requests** : A
   [`DataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter) object is
   part of an API call that describes the data to be selected or returned from
   an API request.

   A single `DataFilter` object can only specify one type of filter criteria to
   locate data:
   - [`developerMetadataLookup`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter#FIELDS.developer_metadata_lookup):
     Selects data associated with the specified developer metadata matching the
     criteria.

   - [`a1Range`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter#FIELDS.a1_range):
     Selects data that matches the specified [A1
     notation](https://developers.google.com/workspace/sheets/api/guides/concepts#a1-notation) range. For
     example, `Sheet1!A1:B10`.

   - [`gridRange`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter#FIELDS.grid_range):
     Selects data that matches the specified grid range using zero-based indexes.
     For example, `Sheet1!A3:B4 == sheetId: 123456, startRowIndex: 2,
     endRowIndex: 4, startColumnIndex: 0, endColumnIndex: 2`.

   To filter across multiple locations or criteria, you can use multiple
   `DataFilter` objects in a single API request. Provide an array or list
   of `DataFilter` objects to a batch request like the
   [`spreadsheets.values.batchGetByDataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGetByDataFilter)
   method. Any range that matches any of the data filters in the request will
   be returned or modified.

   For more information, see [Read and write values associated with
   metadata](https://developers.google.com/workspace/sheets/api/guides/metadata#read-write-associated-metadata).

## Use cases

The following are some example use cases for managing metadata:

- **Associate arbitrary data with various entities and locations in a
  spreadsheet** : For example, associate `totals` with column D, or `responseId
  = 1234` with row 7.

- **Find all locations and data associated with a particular metadata key or
  attribute** : For example, given the key `totals` associated with column D or
  given the `responseId`, return all rows with the `responseId` metadata and
  the metadata value associated with them.

- **Find all data associated with a particular entity or location**: For
  example, given column D, return all metadata associated with that location.

- **Retrieve values in a location by specifying associated metadata** : For
  example, given the `totals` return a representation of the values contained
  in the associated column or row or given a `summary` return a representation
  of the associated Sheet resource.

- **Update values in a location by specifying associated metadata** : For
  example, instead of updating the values in a row through [A1
  notation](https://developers.google.com/workspace/sheets/api/guides/concepts#a1-notation), update values
  by indicating a metadata ID.

## Read and write metadata

The
[`spreadsheets.developerMetadata`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata)
resource provides access to metadata associated with a location or object in a
spreadsheet. Developer metadata can be used to associate arbitrary data with various parts of a spreadsheet. The metadata remains associated at those locations as the spreadsheet is edited.

### Create metadata

To create metadata, use the
[`batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method on the
[`spreadsheets`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets) resource,
and supply a
[`CreateDeveloperMetadataRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#createdevelopermetadatarequest)
with `metadataKey`, `location`, and `visibility` values from the
`spreadsheets.developerMetadata` resource. You can optionally specify a
`metadataValue` or an explicit `metadataId`.

If you specify an ID that's already in use, the request will be unsuccessful. If
you don't supply an ID, the API assigns one.
In this example, we provide a key, value, and a row in the request. The response returns these developer metadata values, plus the assigned metadata ID.

**Request**

```
{
  "requests": [
    {
      "createDeveloperMetadata": {
        "developerMetadata": {
          "location": {
            "dimensionRange": {
              "sheetId": SHEET_ID,
              "dimension": "ROWS",
              "startIndex": 6,
              "endIndex": 7
            }
          },
          "visibility": "DOCUMENT",
          "metadataKey": "Sales",
          "metadataValue": "2022"
        }
      }
    }
  ]
}
```

**Response**

```
{
  "spreadsheetId": SPREADSHEET_ID,
  "replies": [
    {
      "createDeveloperMetadata": {
        "developerMetadata": {
          "metadataId": METADATA_ID,
          "metadataKey": "Sales",
          "metadataValue": "2022",
          "location": {
            "locationType": "ROW",
            "dimensionRange": {
              "sheetId": SHEET_ID,
              "dimension": "ROWS",
              "startIndex": 6,
              "endIndex": 7
            }
          },
          "visibility": "DOCUMENT"
        }
      }
    }
  ]
}
```

### Read a single metadata item

To retrieve a single, distinct developer metadata, use the
[`spreadsheets.developerMetadata.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata/get)
method, specifying the `spreadsheetId` containing the metadata and the developer
metadata's unique `metadataId`.
**Request**

In this example, we provide the spreadsheet ID and metadata ID in the request. The response returns the developer metadata values for the metadata ID.

```
GET https://sheets.googleapis.com/v4/spreadsheets/SPREADSHEET_ID/developerMetadata/METADATA_ID
```

**Response**

```
{
  "metadataId": METADATA_ID,
  "metadataKey": "Sales",
  "metadataValue": "2022",
  "location": {
    "locationType": "ROW",
    "dimensionRange": {
      "sheetId": SHEET_ID,
      "dimension": "ROWS",
      "startIndex": 6,
      "endIndex": 7
    }
  },
  "visibility": "DOCUMENT"
}
```

### Read multiple metadata items

To retrieve multiple items of developer metadata, use the
[`spreadsheets.developerMetadata.search`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata/search)
method. You must specify a
[`DataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter) that matches
any existing metadata on any combination of properties such as key, value,
location, or visibility.
In this example, we provide multiple metadata IDs in the request. The response returns the developer metadata values for each metadata ID.

**Request**

```
{
  "dataFilters": [
    {
      "developerMetadataLookup": {
        "metadataId": METADATA_ID
      }
    },
    {
      "developerMetadataLookup": {
        "metadataId": METADATA_ID
      }
    }
  ]
}
```

**Response**

```
{
  "matchedDeveloperMetadata": [
    {
      "developerMetadata": {
        "metadataId": METADATA_ID,
        "metadataKey": "Revenue",
        "metadataValue": "2022",
        "location": {
          "locationType": "SHEET",
          "sheetId": SHEET_ID
        },
        "visibility": "DOCUMENT"
      },
      "dataFilters": [
        {
          "developerMetadataLookup": {
            "metadataId": METADATA_ID
          }
        }
      ]
    },
    {
      "developerMetadata": {
        "metadataId": METADATA_ID,
        "metadataKey": "Sales",
        "metadataValue": "2022",
        "location": {
          "locationType": "SHEET",
          "sheetId": SHEET_ID
        },
        "visibility": "DOCUMENT"
      },
      "dataFilters": [
        {
          "developerMetadataLookup": {
            "metadataId": METADATA_ID
          }
        }
      ]
    }
  ]
}
```

### Update metadata

To update developer metadata, use the
[`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method and supply an
[`UpdateDeveloperMetadataRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#updatedevelopermetadatarequest).
You must specify a
[`DataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter) that targets
the metadata to be updated, a
[`spreadsheets.developerMetadata`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata)
resource with the new values, and a [field
mask](https://developers.google.com/workspace/sheets/api/guides/field-masks) describing the fields to be
updated.
In this example, we provide the metadata ID, sheet ID, and a new metadata key in the request. The response returns these developer metadata values, plus the updated metadata key.

**Request**

```
{
  "requests": [
    {
      "updateDeveloperMetadata": {
        "dataFilters": [
          {
            "developerMetadataLookup": {
              "metadataId": METADATA_ID
            }
          }
        ],
        "developerMetadata": {
          "location": {
            "sheetId": SHEET_ID
          },
          "metadataKey": "SalesUpdated"
        },
        "fields": "location,metadataKey"
      }
    }
  ]
}
```

**Response**

```
{
  "spreadsheetId": SPREADSHEET_ID,
  "replies": [
    {
      "updateDeveloperMetadata": {
        "developerMetadata": [
          {
            "metadataId": METADATA_ID,
            "metadataKey": "SalesUpdated",
            "metadataValue": "2022",
            "location": {
              "locationType": "SHEET",
              "sheetId": SHEET_ID
            },
            "visibility": "DOCUMENT"
          }
        ]
      }
    }
  ]
}
```

### Delete metadata

To delete developer metadata, use the
[`batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method, and supply a
[`DeleteDeveloperMetadataRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#deletedevelopermetadatarequest).
You must specify a
[`DataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter) to select the
metadata you want to delete.
In this example, we provide the metadata ID in the request. The response returns the developer metadata values for the metadata ID.

To confirm the developer metadata is removed, use the [`spreadsheets.developerMetadata.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata/get)
method, specifying the deleted metadata ID. You should receive a `404: Not Found` HTTP status code response, with a message stating "No developer metadata with ID <var translate="no">METADATA_ID</var>.

**Request**

```
{
  "requests": [
    {
      "deleteDeveloperMetadata": {
        "dataFilter": {
          "developerMetadataLookup": {
            "metadataId": METADATA_ID
          }
        }
      }
    }
  ]
}
```

**Response**

```
{
  "spreadsheetId": SPREADSHEET_ID,
  "replies": [
    {
      "deleteDeveloperMetadata": {
        "deletedDeveloperMetadata": [
          {
            "metadataId": METADATA_ID,
            "metadataKey": "SalesUpdated",
            "metadataValue": "2022",
            "location": {
              "locationType": "SHEET",
              "sheetId": SHEET_ID
            },
            "visibility": "DOCUMENT"
          }
        ]
      }
    }
  ]
}
```

## Read and write values associated with metadata

You can also retrieve and update cell values in rows and columns by specifying
the associated developer metadata and the values you want to update. To do this,
use one of the following methods with a matching
[`DataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/DataFilter).

### Get cell values by metadata

To get cell values by metadata, use the
[`spreadsheets.values.batchGetByDataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGetByDataFilter)
method. You must specify the spreadsheet ID and one or more data filters that
match the metadata.
In this example, we provide the metadata ID in the request. The response returns the row cell values (model number, monthly sales) for the metadata ID.

**Request**

```
{
  "dataFilters": [
    {
      "developerMetadataLookup": {
        "metadataId": METADATA_ID
      }
    }
  ],
  "majorDimension": "ROWS"
}
```

**Response**

```
{
  "spreadsheetId": SPREADSHEET_ID,
  "valueRanges": [
    {
      "valueRange": {
        "range": "Sheet7!A7:Z7",
        "majorDimension": "ROWS",
        "values": [
          [
            "W-24",
            "74"
          ]
        ]
      },
      "dataFilters": [
        {
          "developerMetadataLookup": {
            "metadataId": METADATA_ID
          }
        }
      ]
    }
  ]
}
```

### Get spreadsheet by metadata

When retrieving a spreadsheet, you can return a subset of data by using the
[`spreadsheets.getByDataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/getByDataFilter)
method. You must specify the spreadsheet ID and one or more data filters that
match the metadata.

This request functions as a regular "spreadsheet GET" request except the list of
metadata matched by the specified data filters determines what sheets, grid
data, and other object resources with metadata are returned. If
[`includeGridData`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/getByDataFilter#body.request_body.FIELDS.include_grid_data)
is set to `true`, grid data intersecting the specified grid ranges is also
returned for the sheet. The `includeGridData` field is ignored if a [field
mask](https://developers.google.com/workspace/sheets/api/guides/field-masks) is set in the request.
In this example, we provide the metadata ID and set `includeGridData` to `false` in the request. The response returns both the spreadsheet and sheet properties.

**Request**

```
{
  "dataFilters": [
    {
      "developerMetadataLookup": {
        "metadataId": METADATA_ID
      }
    }
  ],
  "includeGridData": false
}
```

**Response**

```
{
  "spreadsheetId": SPREADSHEET_ID,
  "properties": {
    "title": "Sales Sheet",
    "locale": "en_US",
    "autoRecalc": "ON_CHANGE",
    "timeZone": "America/Los_Angeles",
    "defaultFormat": {
      "backgroundColor": {
        "red": 1,
        "green": 1,
        "blue": 1
      },
      "padding": {
        "top": 2,
        "right": 3,
        "bottom": 2,
        "left": 3
      },
      "verticalAlignment": "BOTTOM",
      "wrapStrategy": "OVERFLOW_CELL",
      "textFormat": {
        "foregroundColor": {},
        "fontFamily": "arial,sans,sans-serif",
        "fontSize": 10,
        "bold": false,
        "italic": false,
        "strikethrough": false,
        "underline": false,
        "foregroundColorStyle": {
          "rgbColor": {}
        }
      },
      "backgroundColorStyle": {
        "rgbColor": {
          "red": 1,
          "green": 1,
          "blue": 1
        }
      }
    },
    "spreadsheetTheme": {
      "primaryFontFamily": "Arial",
      "themeColors": [
        {
          "colorType": "TEXT",
          "color": {
            "rgbColor": {}
          }
        },
        {
          "colorType": "BACKGROUND",
          "color": {
            "rgbColor": {
              "red": 1,
              "green": 1,
              "blue": 1
            }
          }
        },
        {
          "colorType": "ACCENT1",
          "color": {
            "rgbColor": {
              "red": 0.25882354,
              "green": 0.52156866,
              "blue": 0.95686275
            }
          }
        },
        {
          "colorType": "ACCENT2",
          "color": {
            "rgbColor": {
              "red": 0.91764706,
              "green": 0.2627451,
              "blue": 0.20784314
            }
          }
        },
        {
          "colorType": "ACCENT3",
          "color": {
            "rgbColor": {
              "red": 0.9843137,
              "green": 0.7372549,
              "blue": 0.015686275
            }
          }
        },
        {
          "colorType": "ACCENT4",
          "color": {
            "rgbColor": {
              "red": 0.20392157,
              "green": 0.65882355,
              "blue": 0.3254902
            }
          }
        },
        {
          "colorType": "ACCENT5",
          "color": {
            "rgbColor": {
              "red": 1,
              "green": 0.42745098,
              "blue": 0.003921569
            }
          }
        },
        {
          "colorType": "ACCENT6",
          "color": {
            "rgbColor": {
              "red": 0.27450982,
              "green": 0.7411765,
              "blue": 0.7764706
            }
          }
        },
        {
          "colorType": "LINK",
          "color": {
            "rgbColor": {
              "red": 0.06666667,
              "green": 0.33333334,
              "blue": 0.8
            }
          }
        }
      ]
    }
  },
  "sheets": [
    {
      "properties": {
        "sheetId": SHEET_ID,
        "title": "Sheet7",
        "index": 7,
        "sheetType": "GRID",
        "gridProperties": {
          "rowCount": 1000,
          "columnCount": 26
        }
      }
    }
  ],
  "spreadsheetUrl": SPREADSHEET_URL
}
```

### Update values by metadata

To update cell values matching specific metadata, use the
[`spreadsheets.values.batchUpdateByDataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdateByDataFilter)
method. You must specify the spreadsheet ID,
[`valueInputOption`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueInputOption),
and one or more
[`DataFilterValueRange`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdateByDataFilter#datafiltervaluerange)
values that match the metadata.
In this example, we provide the metadata ID and updated row values in the request. The response returns both the updated properties and data for the metadata ID.

**Request**

```
{
  "data": [
    {
      "dataFilter": {
        "developerMetadataLookup": {
          "metadataId": METADATA_ID
        }
      },
      "majorDimension": "ROWS",
      "values": [
        [
          "W-24",
          "84"
        ]
      ]
    }
  ],
  "includeValuesInResponse": true,
  "valueInputOption": "USER_ENTERED"
}
```

**Response**

```
{
  "spreadsheetId": SPREADSHEET_ID,
  "totalUpdatedRows": 1,
  "totalUpdatedColumns": 2,
  "totalUpdatedCells": 2,
  "totalUpdatedSheets": 1,
  "responses": [
    {
      "updatedRange": "Sheet7!A7:B7",
      "updatedRows": 1,
      "updatedColumns": 2,
      "updatedCells": 2,
      "dataFilter": {
        "developerMetadataLookup": {
          "metadataId": METADATA_ID
        }
      },
      "updatedData": {
        "range": "Sheet7!A7:Z7",
        "majorDimension": "ROWS",
        "values": [
          [
            "W-24",
            "84"
          ]
        ]
      }
    }
  ]
}
```

### Clear values by metadata

To clear cell values matching specific metadata, use the
[`spreadsheets.values.batchClearByDataFilter`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchClearByDataFilter)
method. You must specify a data filter to select the metadata you want to clear.
**Request**

In this example, we provide the metadata ID in the request. The response returns the spreadsheet ID and the cleared ranges.

```
{
  "dataFilters": [
    {
      "developerMetadataLookup": {
        "metadataId": METADATA_ID
      }
    }
  ]
}
```

**Response**

```
{
  "spreadsheetId": SPREADSHEET_ID,
  "clearedRanges": [
    "Sheet7!A7:Z7"
  ]
}
```

## Metadata storage limits

There's a limit on the total amount of metadata you can store in a spreadsheet.
This limit is measured in characters and is made up of two components:

| Item | Storage limit allocation |
|---|---|
| Spreadsheet | 30,000 characters |
| Each sheet within a spreadsheet | 30,000 characters |

You can store up to 30,000 characters for the spreadsheet. In addition, you can
store 30,000 characters for each sheet within a spreadsheet (30,000 for sheet
one, 30,000 for sheet two, and so forth). So a spreadsheet with three sheets
could contain up to 120,000 characters of metadata.

Each character in the `metadataKey` and `metadataValue` fields of the
[`spreadsheets.developerMetadata`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.developerMetadata)
resource count toward this limit.

## Related topics

- [Apply filters to your Google Sheets data](https://developers.google.com/workspace/sheets/api/guides/filters-overview)
- [Manage data visibility with filters](https://developers.google.com/workspace/sheets/api/guides/filters)
- [Usage limits](https://developers.google.com/workspace/sheets/api/limits)


# Date and number formats

[Video](https://www.youtube.com/watch?v=86q5TMzvRqo)

This document describes how to define new date and number format patterns, which
you can include in your API request.

Date-time and number formats let you control how your data appears in a sheet.
Google Sheets provides some common formats to choose from, but you can also
define your own formats.

In the Sheets UI, you apply number and date formats to cells
using the
**Format** \> **Number** menu. In the Google Sheets API, you set these formats using
a
[`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method call to send an
[`UpdateCellsRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#updatecellsrequest)
or
[`RepeatCellRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#repeatcellrequest).

The sample [Set a custom datetime or decimal format for a
range](https://developers.google.com/workspace/sheets/api/samples/formatting#custom-format) shows how to set
a format pattern using the Sheets API. Note that the actual rendering
of the format depends on the spreadsheet's
[`locale`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#SpreadsheetProperties.FIELDS.locale).
This document assumes the `locale` is `en_US`. You can determine the `locale` of
a spreadsheet by reading the
[`SpreadsheetProperties`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#spreadsheetproperties)
object using the
[`spreadsheets.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
method on the
[`spreadsheets`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets) resource.

For more samples, see [Basic
formatting](https://developers.google.com/workspace/sheets/api/samples/formatting) and [Conditional
formatting](https://developers.google.com/workspace/sheets/api/samples/conditional-formatting).

## About date and time values

Sheets, like most other spreadsheet applications, treats date and
time values as decimal values. This lets you perform arithmetic on them in
formulas, so you can increment days or weeks, add or subtract two dates and
times, and perform other similar operations.

Sheets uses a form of [epoch
date](https://en.wikipedia.org/wiki/Epoch_(computing)) that's commonly used in
spreadsheets. The whole number portion of the value (left of the decimal) counts
the days since December 30, 1899. The fractional portion (right of the decimal)
counts the time as a fraction of one day. For example, January 1, 1900 at noon
is `2.5`, `2` because it's 2 days after December 30, 1899, and `0.5` because
noon is half a day. February 1, 1900 at 3 PM is `33.625`.

Sheets correctly treats the year 1900 as a common year, not a
leap year.

> [!NOTE]
> **Note:** When you read cell values, you can get dates rendered as strings instead of as serial values by using the [`spreadsheet.values.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get) method together with the [`dateTimeRenderOption`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get#body.QUERY_PARAMETERS.date_time_render_option) query parameter.

## Date and time format patterns

A *date-time format pattern* is a string of token substrings that, when parsed,
are replaced with the corresponding date-time elements (such as the month or
hour).

### Date and time format tokens

The following table defines the token substrings you can use in a date-time
format pattern. A `+` character indicates that the previous character can appear
one or more times and still match the pattern. Characters not listed in this
table are treated as literals, and are output without changes.

| Token | Description |
|---|---|
| `h` | Hour of the day. Switches between 12 and 24-hour format depending on whether an AM or PM indicator is present in the string. |
| `hh+` | Same as previous, but with a leading 0 for 1-9. |
| `m` | If the previous non-literal token was hours or the subsequent one is seconds, it represents minutes in the hour (without a leading 0). Otherwise, it represents the month of the year as a number (without a leading 0). |
| `M` | The month of the year without a leading 0. Use this token to expressly state a month, instead of a minute, in your pattern. |
| `mm` | Same as `m`, but with a leading 0 for both cases. |
| `MM` | The month of the year with a leading 0. Use this token to expressly state a month, instead of a minute, in your pattern. |
| `mmm` | Three letter month abbreviation (for example, "Feb"). |
| `mmmm` | Full month name. `mmmmmm+` also matches this. |
| `mmmmm` | First letter of the month (for example, "J" for June). |
| `s` | Seconds in the minute without a leading 0. |
| `ss` | Seconds in the minute with a leading 0. |
| `[h+]` | Number of elapsed hours in a time duration. Number of letters indicates minimum number of digits (adds leading 0s). |
| `[m+]` | Number of elapsed minutes in a time duration. Number of letters indicates minimum number of digits (adds leading 0s). |
| `[s+]` | Number of elapsed seconds in a time duration. Number of letters indicates minimum number of digits (adds leading 0s). |
| `d` | Day of the month, without a leading 0 for numbers less than 10. |
| `dd` | Day of the month, with a leading 0 for numbers less than 10. |
| `ddd` | Day of the week, three-letter abbreviation (for example, "Mon"). |
| `dddd+` | Day of the week, full name. |
| `y` | 2-digit year. |
| `yy` | 2-digit year. |
| `yyy` | 4-digit year. |
| `yyyy+` | 4-digit year. |
| `a/p` | Displays "a" for AM, and "p" for PM. Also changes hours to 12-hour format. If the token letter is capitalized, the output is, too. |
| `am/pm` | As above, but displays "AM" or "PM" instead and is always capitalized. |
| `0` | Tenths of seconds. You can increase the precision to two digits with `00` or three digits (milliseconds) with `000`. |
| ` \ ` | Treats the next character as a literal value and not any special meaning it might have. |
| `"text"` | Displays whatever text is inside the quotation marks as a literal. |

### Date and time format examples

Given the date and time `Tuesday, April 5, 2016, 4:08:53.528 PM`, the
following table shows some example patterns and their corresponding date-time
rendering. The second section of the table shows examples of elapsed time
formats for an elapsed time of `3 hours, 13 minutes, 41.255 seconds`:

| Date-time Patterns | `Tuesday, April 5, 2016, 4:08:53.528 PM` |
|---|---|
| `h:mm:ss.00 a/p` | 4:08:53.53 p |
| `hh:mm A/P".M."` | 04:08 P.M. |
| `yyyy-mm-dd` | 2016-04-05 |
| `mmmm d \[dddd\]` | April 5 \[Tuesday\] |
| `h PM, ddd mmm dd` | 4 PM, Tue Apr 05 |
| `dddd, m/d/yy at h:mm` | Tuesday, 4/5/16 at 16:08 |
| `[hh]` | 03 |
| `[mmmm]` | 0193 |
| `[ss]` | 11621 |

## Number format patterns

A *number format pattern* is a string of token substrings that, when parsed, are
replaced with the corresponding number representations. A number format pattern
can consist of up to four sections, separated by semicolons, which define the
separate formats used for positive numbers, negative numbers, zero, and text (in
that order):

```
[POSITIVE FORMAT];[NEGATIVE FORMAT];[ZERO FORMAT];[TEXT FORMAT]
```

You don't need to include all four sections in a format. If you only include one
section, that format is used for all values. Using two sections causes the first
format to be applied to zero and positive numbers and the second format to
negative numbers. Using three sections defines separate formats for positive,
negative, and zero numbers. For example:

```
[NUMBER FORMAT]
[POSITIVE/ZERO FORMAT];[NEGATIVE FORMAT]
[POSITIVE FORMAT];[NEGATIVE FORMAT];[ZERO FORMAT]
```

However, if there are two or more sections and the final section is a text
format, that section is treated as the text format and the others behave as if
there's one less section. Thus, by including a final text format it's possible
to define formats such as:

```
[POSITIVE/ZERO FORMAT];[NEGATIVE FORMAT];[TEXT FORMAT]
[NUMBER FORMAT];[TEXT FORMAT]
```

The parsing of a format into sections occurs before other parsing, so it's
possible to have a date or time format included as one of the sections (though
this is of limited utility).

### Number format tokens

The following table defines the token substrings you can use in a format section
to define how to represent the values for that section.

| Token | Description |
|---|---|
| `0` | Represents a digit in the number. If the digit is an insignificant 0, it's rendered as 0. For example, the number format `00.0` renders the number 3 as "`03.0`". |
| `#` | Represents a digit in the number. If the digit is an insignificant 0, it's not rendered. For example, the number format `##0` renders the number 12 as "`12`". |
| `?` | Represents a digit in the number. If the digit is an insignificant 0, it's rendered as a space. This is often used to align the decimal point in a column when using fixed-width fonts. For example, the number format `???.???` renders the number 12.4 as: " `12.4` ". |
| `.` | The first period represents the decimal point in the number. Subsequent periods are rendered as literals. If you include a decimal point in the format, it's always rendered, even for whole numbers. For example, `#0.#` renders the number 3 as "`3.`". |
| `%` | Appears as a literal but also causes existing numbers to be multiplied by 100 before being rendered to make percentages more readable. For example, the number format `#%` renders the number 0.25 as "`25%`". |
| `,` | If it appears between two-digit characters (`0`, `#`, or `?`), it renders the entire number with grouping separators (grouping by the thousands). If it follows the digit characters, it scales the digits by one thousand per comma (for example, the format `#0.0,,` renders the number 12,200,000 as `12.2`). |
| `E-` | Renders the number in scientific format, with the formatting to the left of the `E` used for the non-exponent portion and the formatting to the right of the E used for the exponent portion. `E+` shows a `+` sign for positive exponents. `E-` only shows a sign for negative exponents. If lowercase is used, the output `e` is in lowercase as well. For example, the number format `##0.00#E+##` renders the number 0.0000001 as "`100.00E-9`". |
| `E+` | Renders the number in scientific format, with the formatting to the left of the `E` used for the non-exponent portion and the formatting to the right of the E used for the exponent portion. `E+` shows a `+` sign for positive exponents. `E-` only shows a sign for negative exponents. If lowercase is used, the output `e` is in lowercase as well. For example, the number format `##0.00#E+##` renders the number 0.0000001 as "`100.00E-9`". |
| `e-` | Renders the number in scientific format, with the formatting to the left of the `E` used for the non-exponent portion and the formatting to the right of the E used for the exponent portion. `E+` shows a `+` sign for positive exponents. `E-` only shows a sign for negative exponents. If lowercase is used, the output `e` is in lowercase as well. For example, the number format `##0.00#E+##` renders the number 0.0000001 as "`100.00E-9`". |
| `e+` | Renders the number in scientific format, with the formatting to the left of the `E` used for the non-exponent portion and the formatting to the right of the E used for the exponent portion. `E+` shows a `+` sign for positive exponents. `E-` only shows a sign for negative exponents. If lowercase is used, the output `e` is in lowercase as well. For example, the number format `##0.00#E+##` renders the number 0.0000001 as "`100.00E-9`". |
| `/` | If it appears between two-digit characters (`0`, `#`, or `?`), it treats those digit groups as a fractional format. For example, the number format `0 #/#` renders the number 23.25 as `23 1/4`. The denominator can also be a literal integer, in which case it enforces that integer as the denominator. The number format `0 #/8` displays the number 23.25 as `23 2/8`. The fraction part is not rendered if the numerator becomes 0. The number 23.1 with the number format `0 #/3` renders as just `23` (because the 0.1 is rounded to 0/3). `/` is not compatible with scientific format or a format with a decimal point in it. |
| `*` | This is included for compatibility with Microsoft Excel number formats. It's ignored. |
| `_` | The underscore token skips the next character and renders a space. This is used to line up number formats where the negative value is surrounded by parentheses. |
| ` \ ` | Treats the next character as a literal value and not any special meaning it might have. For example, the number format `\#0` renders the number 10 as "`#10`". |
| `"text"` | Displays whatever text is inside the quotation marks as a literal. |
| `@` | If the cell has text input, it inserts the raw text for the cell. Not compatible with any of the other special characters and it doesn't display for numeric values (which are displayed as general format). |
| `$ - + ( ) : space` | Sheets treats these characters as literals and displays them unchanged. |

### Meta instructions

In addition, each of the format sections can have optional meta instructions,
enclosed in `[]` characters, that precede the format and provide additional
instructions. There are two meta instruction types and a given section can use
both:

| Instruction | Description |
|---|---|
| `[condition]` | Replaces the default positive, negative, or zero comparison of the section with another conditional expression. For example, `[<100]"Low";[>1000]"High";000` renders the word "Low" for values below 100, "High" for values above 1000, and a three-digit number (with leading 0s) for anything in between. Conditions can only be applied to the first two sub-formats and if a number matches more than one, it uses the first one it matches. If there's a third format, it's used for "everything else", otherwise if a number doesn't match either format, it's rendered as all "#"s filling up the cell width. If it exists, the fourth format is always used for text. |
| `[Color] or [Color#]` | Causes any value this sub-format renders to appear with the given text color. Valid values for <var translate="no">Color</var> are Black, Blue, Cyan, Green, Magenta, Red, White, or Yellow. Valid values for the "#" in <var translate="no">Color#</var> are from 1 to 56. Number format colors override any user-entered colors on the cell, but don't override colors set by conditional formatting. This [color palette](https://dmcritchie.mvps.org/excel/colors.htm) shows a list of colors that correspond to each number. They're based on the [`ColorIndex`](https://learn.microsoft.com/en-us/office/vba/api/excel.colorindex) property in Microsoft Excel. |

### Number format examples

The following table shows some example patterns and their corresponding
formatted number rendering:

| Number | Pattern | Formatted Value |
|---|---|---|
| 12345.125 | `####.#` | 12345.1 |
| 12.34 | `000.0000` | 012.3400 |
| 12 | `#.0#` | 12.0 |
| 5.125 | `# ???/???` | 5 1/8 |
| 12000 | `#,###` | 12,000 |
| 1230000 | `0.0,,"M"` | 1.2M |
| 1234500000 | `0.00e+00` | 1.23e+09 |
| 123114.15115 MyText | `###0.000;"TEXT: "_(@_)` | 123114.151 TEXT: MyText |
| 1234 -1234 0 MyText | `[Blue]#,##0;[Red]#,##0;[Green]0.0;[Magenta]_(@_)` | 1,234 1,234 0.0 MyText |
| 1005 32 527 | `[>1000]"HIGH";[Color43][<=200]"LOW";0000` | HIGH LOW 0527 |
| `[>1000]"HIGH";[Color43][<=200]"LOW";0000` |
| `[>1000]"HIGH";[Color43][<=200]"LOW";0000` |

## Related topics

- [Samples overview](https://developers.google.com/workspace/sheets/api/samples)



# Smart chips

This guide describes how and why to use the Google Sheets API to add and manage smart chips in your spreadsheets.

## What is a smart chip?

With smart chips, you can bring rich, interactive data from other Google Workspace applications directly into Sheets. Smart chips provide users with quick access to information and actions, transforming a cell link into a dynamic object. To learn more, see [Insert smart chips in your Google Sheets](https://support.google.com/docs/answer/12319513).

## Add a smart chip

Chips are represented as chip runs, which are part of [CellData](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/cells#CellData), so you can insert a chip using the existing [`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate) method by supplying an [`UpdateCellsRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#UpdateCellsRequest) or [`AppendCellsRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#AppendCellsRequest).

The request must include the `chipRuns` field. A chip run describes a chip's properties and where it is located within the cell's text.

When writing `chipRuns`, you must also provide a `userEnteredValue` that contains placeholder characters (`@`) for each chip you intend to add. Each run in the `chipRuns` array will correspond to one of the placeholders. Non-chipped runs can be omitted on write.

The following example shows how to write a file chip, a people chip, and plain text into a single cell, A1.
\* {JSON}

            {
              "updateCells": {
                "rows": [
                  {
                    "values": [
                      {
                        "userEnteredValue": {
                          "stringValue": "@ is the owner of @."
                        },
                        "chipRuns": [
                          {
                            "chip": {
                              "personProperties": {
                                "email": "johndoe@gmail.com",
                                "displayFormat": "DEFAULT"
                              }
                            }
                          },
                          {
                            "startIndex": 18,
                            "chip": {
                              "richLinkProperties": {
                                "uri": "https://docs.google.com/document/d/YOUR_DOCUMENT_ID/edit"
                              }
                            }
                          }
                        ]
                      }
                    ]
                  }
                ],
                "fields": "userEnteredValue,chipRuns",
                "range": {
                  "startRowIndex": 0,
                  "startColumnIndex": 0
                }
              }
            }

### Configure chip properties

A chip object can have one of the following property types:

#### personProperties

Use this to create a person chip.

- `email`: (Required) The email address of the person to link.
- `displayFormat`: (Optional) The preferred display format for the person's name. Can be one of the following:
  - `DEFAULT`: Standard "First Name Last Name" format.
  - `LAST_NAME_COMMA_FIRST_NAME`: "Last Name, First Name" format.
  - `EMAIL`: The person's email address.

#### richLinkProperties

Use this to create a rich link chip. While the API can read links to various Google services (like YouTube or Calendar), only links to Google Drive files can be written as chips.

- `uri`: (Required) The URI of the resource. For writing, this must be a Google Drive file link. URIs cannot exceed 2000 bytes.

> [!NOTE]
> **Note:** Writing Drive file chips requires your application to be authorized with at least one of the `drive.file`, `drive.readonly`, or `drive` OAuth scopes.

## Read a smart chip

To read a smart chip's data, use the [`spreadsheets.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get) method and include `sheets.data.rowData.values(chipRuns)` in the fields parameter.

The `chipRuns` array in the response will contain objects for all subsections of the cell's text.

- Chipped runs: These sections will have a populated `chip` object containing either `personProperties` or `richLinkProperties`.
- Non-chipped runs: Plain text sections will also have a run object, but its `chip` field will be empty.

To get the display text of a chip, add `formattedValue` as a field.

## Update a smart chip

To update or replace a smart chip, you have to overwrite the cell's contents. Use the same [`UpdateCellsRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#UpdateCellsRequest) or [`AppendCellsRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#AppendCellsRequest) as you would for adding a chip, providing a new `userEnteredValue` and `chipRun`. This will replace the existing chip in the cell.

> [!IMPORTANT]
> **Important:** Writing a new `userEnteredValue` to a cell will erase all previous `chipRuns` from that cell. You must supply a new set of runs to persist or modify the chips.

## Use smart chips with other Sheets features

[Tables](https://developers.google.com/workspace/sheets/api/guides/tables): Smart chips can be used as a table column type to track project owners (People chip), or related documents (File chip).

[Filters](https://developers.google.com/workspace/sheets/api/guides/filters): You can filter ranges based on the text value of the smart chips within them.


# Tables

This guide describes how and why to use the Google Sheets API to create tables
in your spreadsheets.

## What is a table?

With tables, you can simplify data creation and reduce the need to repeatedly
format, input, and update data by automatically applying format and structure to
ranges of data.

Tables have many features, such as headers, footers, column types, filters,
views, table references, and table names. See more in the
[feature announcement](https://workspaceupdates.googleblog.com/2024/05/tables-in-google-sheets.html),
and [learn about using tables in Sheets](https://support.google.com/docs/answer/14239833?hl).

You can use tables for tasks such as Project Tracking, Event Planning, and
Inventory management.

## Add a table

To add a table, use the
[batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method, supplying an
[addTable](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#addtablerequest)
request. You use this request to add a table to the spreadsheet.

The following example creates a table named "Project Tracker" at the specified
`range` with 2 set columns. A percent column in the first column and a dropdown
column type in the second column.

    {
      "addTable": {
        "table": {
          "name": "Project Tracker",
          "tableId": "123",
          "range": {
            "sheetId": 0,
            "startColumnIndex": 0,
            "endColumnIndex": 5,
            "startRowIndex": 0,
            "endRowIndex": 5,
          },
          "columnProperties": [
            {
              "columnIndex": 0,
              "columnName": "Column 1",
              "columnType": "PERCENT"
            },
            {
              "columnIndex": 1,
              "columnName": "Column 2",
              "columnType": "DROPDOWN",
              "dataValidationRule": {
                "condition": {
                  "type": "ONE_OF_LIST",
                  "values": [
                    {
                      "userEnteredValue": "Not Started"
                    },
                    {
                      "userEnteredValue": "In Progress"
                    },
                    {
                      "userEnteredValue": "Complete"
                    }
                  ]
                }
              }
            }
          ],
        }
      }
    }

## Column types

Tables have [column types](https://support.google.com/docs/answer/14239833?hl#zippy=%2Ccreate-table-names%2Cset-column-types)
such as numeric, date, dropdown, smart chip, checkbox. The rating and
checkbox column types populate with default values of 0 and FALSE
respectively.

### Dropdown column type

The dropdown column type creates a chip dropdown. If a column type is set as
dropdown, the `dataValidationRule` for the column must be set with a
ONE_OF_LIST condition. Other column types shouldn't set the \`dataValidationRule
field.

## Update a table

Use the [`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method and supply an [UpdateTableRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#updatetablerequest).

### Modify the table size

Use the [UpdateTableRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#updatetablerequest)
method to modify the `range` that the table has to add new rows/columns.

If you need to add a new row or column within the table, use the
[InsertRangeRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#insertrangerequest)
or the
[InsertDimensionRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#insertdimensionrequest)

If you need to delete a table row you can use [DeleteRangeRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#deleterangerequest)
otherwise you can use [DeleteDimensionRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#deletedimensionrequest)
to delete an entire row from the spreadsheet.

> [!NOTE]
> **Note:** If using InsertRangeRequest/DeleteRangeRequest, the range in the request must cover the entire row(s) if deleting a row or entire column(s) if deleting a column.

### Toggle the table footer

If you're updating an existing table without a footer to add a footer, the
`range` expands by 1 row. If you're updating an existing table with a footer
and removing the footer, the `range` shrinks by 1 row.

## Append values to a table

Use [AppendCellsRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#appendcellsrequest)
with `tableId` to add rows to the end of a table. This appends the values to
the first free row and is aware of full rows and footers. If there are no
empty rows, this inserts rows to the end of the table and before any footer,
if applicable.

## Delete a table

Use the [`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method and supply a
[DeleteTableRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#deletetablerequest).
Use the [DeleteTableRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#deletetablerequest)
to delete the entire table and the contents of the table.

Use the [DeleteBandingRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#deletebandingrequest)
to remove the table formatting but keep the data.

## Use tables with other Sheets features

Other API features that support tables being their backing data include
[filters](https://developers.google.com/workspace/sheets/api/guides/filters#basic-filter),
[filter views](https://developers.google.com/workspace/sheets/api/guides/filters#filter-views), and
[protected ranges](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#protectedrange).



# Conditional formatting lets you format cells so that their appearance changes
dynamically according to the value they contain, or to values in other cells.
There are many possible applications of conditional formatting, including these
uses:

- Highlight cells above a certain threshold (for example, using bold text for all transactions over $2,000).
- Format cells so their color varies with their value (for example, applying a more intense red background as the amount over $2,000 increases).
- Dynamically format cells based on the content of other cells (for example, highlighting the address of properties whose "time on market" field is \> 90 days).

You can even format cells based on their value and those of other cells. For
example, you could format a range of cells based on their value compared to the
median value of the range:

![Formatting to highlight values above or below the median
age.](https://developers.google.com/static/workspace/sheets/images/cond-format-example.png)

**Figure 1.** Formatting to highlight values above or below the median age.

In this example, the cells in each row are formatted according to how the value
in their `age` column compares to the median value of all the ages. Rows whose
age is above the median have red text, and those below the median have a red
background. Two of the rows have a value for `age` that matches the median age
(48) and these cells receive no special formatting. (For the source code that
creates this conditional formatting, see the [Example](https://developers.google.com/workspace/sheets/api/guides/conditional-format#example) below.)

## Conditional formatting rules

Conditional formatting is expressed using *formatting rules*. Each spreadsheet
stores a list of these rules, and applies them in the same order as they appear
in the list. The Google Sheets API lets you add, update, and delete these
formatting rules.

Each rule specifies a target range, type of rule, conditions for triggering the
rule, and any formatting to apply.

**Target range**---This can be a single cell, a range of cells, or multiple
ranges.

**Type of rule**---There are two categories of rules:

- [Boolean rules](https://developers.google.com/workspace/sheets/api/guides/conditional-format#boolean-rules) apply a format only if specific criteria are met.
- [Gradient rules](https://developers.google.com/workspace/sheets/api/guides/conditional-format#gradient-rules) calculate the background color of a cell, based on the value of the cell.

The conditions that are evaluated, and the formats that you can apply, are
different for each of these rule types, as detailed in the following sections.

### Boolean rules

A
[`BooleanRule`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#booleanrule)
defines whether to apply a specific format, based on a
[`BooleanCondition`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/other#booleancondition)
that evaluates to `true` or `false`. A boolean rule takes the form:

    {
      "condition": {
        object(BooleanCondition)
      },
      "format": {
        object(CellFormat)
      },
    }

The condition can use the built-in
[`ConditionType`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/other#conditiontype),
or it can use a custom formula for more complex evaluations.

**Built-in types** let you apply formatting according to numeric thresholds,
text comparison, or whether a cell is populated. For example, `NUMBER_GREATER`
means the cell's value must be greater than the condition's value. Rules are
always evaluated against the target cell.

**Custom formula** is a special condition type that lets you apply formatting
according to an arbitrary expression, which also allows evaluation of any cell,
not just the target cell. The condition's formula must evaluate to `true`.

To define the formatting applied by a boolean rule, you use a subset of the
[`CellFormat`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/cells#cellformat)
type to define:

- Whether the text in the cell is bold, italic, or strikethrough.
- The text color in the cell.
- The background color of the cell.

> [!WARNING]
> Only use the formatting features listed above. Although the `CellFormat` type can express other formatting---including underlining, alignment, or borders---attempting to use these in a conditional formatting rule results in a `400 invalid request` error.

### Gradient rules

A
[`GradientRule`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#gradientrule)
defines a range of colors that correspond to a range of values. A gradient rule
takes the form:

    {
      "minpoint": {
        object(InterpolationPoint)
      },
      "midpoint": {
        object(InterpolationPoint)
      },
      "maxpoint": {
        object(InterpolationPoint)
      },
    }

Each
[`InterpolationPoint`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#interpolationpoint)
defines a color and its corresponding value. A set of three points defines a
color gradient.

## Manage conditional formatting rules

To create, modify, or delete conditional formatting rules, use the
[`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method with the appropriate request type:

- Add rules to the list at the given index using the
  [`AddConditionalFormatRuleRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#addconditionalformatrulerequest).

- Replace or reorder rules in the list at the given index using the
  [`UpdateConditionalFormatRuleRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#updateconditionalformatrulerequest).

- Remove rules from the list at the given index using the
  [`DeleteConditionalFormatRuleRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#deleteconditionalformatrulerequest).

## Example

The following example shows how to create the conditional formatting shown in
the screenshot at the top of this page. For additional examples, see the
[Conditional formatting](https://developers.google.com/workspace/sheets/api/samples/conditional-formatting)
samples page.

### Python

sheets/snippets/sheets_conditional_formatting.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_conditional_formatting.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def conditional_formatting(spreadsheet_id):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)

    my_range = {
        "sheetId": 0,
        "startRowIndex": 1,
        "endRowIndex": 11,
        "startColumnIndex": 0,
        "endColumnIndex": 4,
    }
    requests = [
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [my_range],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [
                                {
                                    "userEnteredValue": (
                                        "=GT($D2,median($D$2:$D$11))"
                                    )
                                }
                            ],
                        },
                        "format": {
                            "textFormat": {"foregroundColor": {"red": 0.8}}
                        },
                    },
                },
                "index": 0,
            }
        },
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [my_range],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [
                                {
                                    "userEnteredValue": (
                                        "=LT($D2,median($D$2:$D$11))"
                                    )
                                }
                            ],
                        },
                        "format": {
                            "backgroundColor": {
                                "red": 1,
                                "green": 0.4,
                                "blue": 0.4,
                            }
                        },
                    },
                },
                "index": 0,
            }
        },
    ]
    body = {"requests": requests}
    response = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    print(f"{(len(response.get('replies')))} cells updated.")
    return response

  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: spreadsheet_id
  conditional_formatting("1CM29gwKIzeXsAppeNwrc8lbYaVMmUclprLuLYuHog4k")
```




# Pivot tables

This guide describes how and why to use the Google Sheets API to create pivot
tables in your spreadsheets.

## What is a pivot table?

Pivot tables provide a way to summarize data in your spreadsheet, automatically
aggregating, sorting, counting, or averaging the data while displaying the
summarized results in a new table. A pivot table acts as a sort of query
against a source dataset. This source data exists at some other location in
the spreadsheet, and the pivot table presents a processed view of the data.

For example, consider the following sales dataset:


|---|---|---|---|---|---|---|---|
|   | A | B | C | D | E | F | G |
| 1 | Item Category | Model Number | Cost | Quantity | Region | Salesperson | Ship Date |
| 2 | Wheel | W-24 | $20.50 | 4 | West | Beth | 3/1/2016 |
| 3 | Door | D-01X | $15.00 | 2 | South | Amir | 3/15/2016 |
| 4 | Engine | ENG-0134 | $100.00 | 1 | North | Carmen | 3/20/2016 |
| 5 | Frame | FR-0B1 | $34.00 | 8 | East | Hannah | 3/12/2016 |
| 6 | Panel | P-034 | $6.00 | 4 | North | Devyn | 4/2/2016 |
| 7 | Panel | P-052 | $11.50 | 7 | East | Erik | 5/16/2016 |
| 8 | Wheel | W-24 | $20.50 | 11 | South | Sheldon | 4/30/2016 |
| 9 | Engine | ENG-0161 | $330.00 | 2 | North | Jessie | 7/2/2016 |
| 10 | Door | D-01Y | $29.00 | 6 | West | Armando | 3/13/2016 |
| 11 | Frame | FR-0B1 | $34.00 | 9 | South | Yuliana | 2/27/2016 |
| 12 | Panel | P-102 | $3.00 | 15 | West | Carmen | 4/18/2016 |
| 13 | Panel | P-105 | $8.25 | 13 | West | Jessie | 6/20/2016 |
| 14 | Engine | ENG-0211 | $283.00 | 1 | North | Amir | 6/21/2016 |
| 15 | Door | D-01X | $15.00 | 2 | West | Armando | 7/3/2016 |
| 16 | Frame | FR-0B1 | $34.00 | 6 | South | Carmen | 7/15/2016 |
| 17 | Wheel | W-25 | $20.00 | 8 | South | Hannah | 5/2/2016 |
| 18 | Wheel | W-11 | $29.00 | 13 | East | Erik | 5/19/2016 |
| 19 | Door | D-05 | $17.70 | 7 | West | Beth | 6/28/2016 |
| 20 | Frame | FR-0B1 | $34.00 | 8 | North | Sheldon | 3/30/2016 |

<br />

You could use a pivot table to create a report showing how many of each model
number were sold in each region:

![screenshot of a pivot table showing count of model number by region](https://developers.google.com/static/workspace/sheets/images/ptable-result.png)

For the source code used to generate this pivot table, see the
[Example](https://developers.google.com/workspace/sheets/api/guides/pivot-tables#example) section below.

Once a pivot table is placed in a spreadsheet, users can interactively change
the summary's structure and details using the Sheets UI.

## Working with pivot tables

A pivot table definition is associated with a single cell on a sheet. Although
its rendered appearance is many cells in both height and width, programmatically
it's located at a single cell coordinate. This cell becomes the top-left
corner of the rendered pivot table, with its horizontal and vertical extent
determined by its definition.

### Adding a pivot table

To add a pivot table, use the
[batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method, supplying an
[updateCells](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#updatecellsrequest)
request. You use this request to supply a
[PivotTable](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/pivot-tables#PivotTable)
definition as the content of a cell as shown below:

    {
      "updateCells": {
        "rows": {
          "values": [
            {
              "pivotTable": "MyPivotTable"
            }
          ],
          "start": {
            "sheetId": "sheetId",
            "rowIndex": 0,
            "columnIndex": 0
          },
          "fields": "pivotTable"
        }
      }
    }

This places the pivot table described by `MyPivotTable` on the specified sheet,
with the top-left corner at cell `A1`. (The height and width of the pivot
table are dynamic; you specify only the origin.)

The
[PivotTable](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/pivot-tables#PivotTable)
type lets you specify:

- Source data range
- One or more fields whose data will form the rows of the pivot table
- One or more fields whose data will form the columns of the pivot table
- Filtering and aggregation criteria
- Pivot table layout

### Modifying and deleting pivot tables

There are no explicit requests to modify or delete a pivot table. Instead, use
an
[updateCells](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#updatecellsrequest)
request with different cell contents:

- To modify a pivot table, create a modified [PivotTable](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/pivot-tables#PivotTable) definition and update the cell using it, similar to adding a new pivot table.
- To delete a pivot table, update the cell with empty values. For an example, see the [Delete a pivot
  table](https://developers.google.com/workspace/sheets/api/samples/pivot-tables#delete_a_pivot_table) sample.

## Use cases

There are many different uses for pivot tables, across a broad range of areas
including statistical analysis, ERP applications, financial reporting, and
others. Classic pivot table use cases include items like:

- Total sales by region and quarter
- Average salary by title and location
- Count of incidents by product and time of day

The number of potential applications of pivot tables is vast, and the ability
to generate them *programmatically* is powerful. You can generate pivot
tables that support interactive exploration but are tailored to specific
circumstances, for example:

- Explore incident data for the most recent 24-hour period
- View or analyze aggregated data corresponding to the selected account
- Examine sales data for territories belonging to the current user

## Example

This example creates a pivot table from a dataset to produce the "model number
by region" report shown in the introduction of this page. For additional
examples, see the [pivot table
sample](https://developers.google.com/workspace/sheets/api/samples/pivot-tables) page.

### Apps Script

sheets/api/spreadsheet_snippets.gs [View on GitHub](https://github.com/googleworkspace/apps-script-samples/blob/main/sheets/api/spreadsheet_snippets.gs)

```javascript
/**
 * Create pivot table
 * @param {string} spreadsheetId spreadsheet ID
 * @returns {*} pivot table's spreadsheet
 */
Snippets.prototype.pivotTable = (spreadsheetId) => {
  try {
    const spreadsheet = SpreadsheetApp.openById(spreadsheetId);

    // Create two sheets for our pivot table, assume we have one.
    const sheet = spreadsheet.getSheets()[0];
    sheet.copyTo(spreadsheet);

    const sourceSheetId = spreadsheet.getSheets()[0].getSheetId();
    const targetSheetId = spreadsheet.getSheets()[1].getSheetId();

    // Create pivot table
    const pivotTable = Sheets.newPivotTable();

    const gridRange = Sheets.newGridRange();
    gridRange.sheetId = sourceSheetId;
    gridRange.startRowIndex = 0;
    gridRange.startColumnIndex = 0;
    gridRange.endRowIndex = 20;
    gridRange.endColumnIndex = 7;
    pivotTable.source = gridRange;

    const pivotRows = Sheets.newPivotGroup();
    pivotRows.sourceColumnOffset = 1;
    pivotRows.showTotals = true;
    pivotRows.sortOrder = "ASCENDING";
    pivotTable.rows = pivotRows;

    const pivotColumns = Sheets.newPivotGroup();
    pivotColumns.sourceColumnOffset = 4;
    pivotColumns.sortOrder = "ASCENDING";
    pivotColumns.showTotals = true;
    pivotTable.columns = pivotColumns;

    const pivotValue = Sheets.newPivotValue();
    pivotValue.summarizeFunction = "COUNTA";
    pivotValue.sourceColumnOffset = 4;
    pivotTable.values = [pivotValue];

    // Create other metadata for the updateCellsRequest
    const cellData = Sheets.newCellData();
    cellData.pivotTable = pivotTable;

    const rows = Sheets.newRowData();
    rows.values = cellData;

    const start = Sheets.newGridCoordinate();
    start.sheetId = targetSheetId;
    start.rowIndex = 0;
    start.columnIndex = 0;

    const updateCellsRequest = Sheets.newUpdateCellsRequest();
    updateCellsRequest.rows = rows;
    updateCellsRequest.start = start;
    updateCellsRequest.fields = "pivotTable";

    // Batch update our spreadsheet
    const batchUpdate = Sheets.newBatchUpdateSpreadsheetRequest();
    const updateCellsRawRequest = Sheets.newRequest();
    updateCellsRawRequest.updateCells = updateCellsRequest;
    batchUpdate.requests = [updateCellsRawRequest];
    const response = Sheets.Spreadsheets.batchUpdate(
      batchUpdate,
      spreadsheetId,
    );

    return response;
  } catch (err) {
    // TODO (developer) - Handle exception
    console.log("Failed with error %s", err.message);
  }
};
```


### Python

sheets/snippets/sheets_pivot_tables.py [View on GitHub](https://github.com/googleworkspace/python-samples/blob/main/sheets/snippets/sheets_pivot_tables.py)

```python
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def pivot_tables(spreadsheet_id):
  """
  Creates the batch_update the user has access to.
  Load pre-authorized user credentials from the environment.
  TODO(developer) - See https://developers.google.com/identity
  for guides on implementing OAuth2 for the application.
  """
  creds, _ = google.auth.default()
  # pylint: disable=maybe-no-member
  try:
    service = build("sheets", "v4", credentials=creds)
    # Create two sheets for our pivot table.
    body = {"requests": [{"addSheet": {}}, {"addSheet": {}}]}
    batch_update_response = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    source_sheet_id = (
        batch_update_response.get("replies")[0]
        .get("addSheet")
        .get("properties")
        .get("sheetId")
    )
    target_sheet_id = (
        batch_update_response.get("replies")[1]
        .get("addSheet")
        .get("properties")
        .get("sheetId")
    )
    requests = []
    requests.append(
        {
            "updateCells": {
                "rows": {
                    "values": [
                        {
                            "pivotTable": {
                                "source": {
                                    "sheetId": source_sheet_id,
                                    "startRowIndex": 0,
                                    "startColumnIndex": 0,
                                    "endRowIndex": 20,
                                    "endColumnIndex": 7,
                                },
                                "rows": [
                                    {
                                        "sourceColumnOffset": 1,
                                        "showTotals": True,
                                        "sortOrder": "ASCENDING",
                                    },
                                ],
                                "columns": [{
                                    "sourceColumnOffset": 4,
                                    "sortOrder": "ASCENDING",
                                    "showTotals": True,
                                }],
                                "values": [{
                                    "summarizeFunction": "COUNTA",
                                    "sourceColumnOffset": 4,
                                }],
                                "valueLayout": "HORIZONTAL",
                            }
                        }
                    ]
                },
                "start": {
                    "sheetId": target_sheet_id,
                    "rowIndex": 0,
                    "columnIndex": 0,
                },
                "fields": "pivotTable",
            }
        }
    )
    body = {"requests": requests}
    response = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    return response

  except HttpError as error:
    print(f"An error occurred: {error}")
    return error


if __name__ == "__main__":
  # Pass: spreadsheet_id
  pivot_tables("1CM29gwKIzeXsAppeNwrc8lbYaVMmUclprLuLYuHog4k")
```


# Migrate from Sheets API v3

> [!IMPORTANT]
> **Important:** The Google Sheets API v3 was turned down on August 2, 2021. Use this guide to migrate to Sheets API v4. For more information about this change, [read the blog post](https://cloud.google.com/blog/products/g-suite/migrate-your-apps-use-latest-sheets-api)

If you have existing apps based on the Google Sheets API v3, you can migrate to
Google Sheets API v4. The v4 version is JSON-based, has an easier-to-use
interface, and adds a substantial amount of functionality that is not possible
in the v3 version.

This page provides a mapping between the older Sheets API v3 commands
and their equivalent operations in Sheets API v4. The mapping focuses
largely on the
[spreadsheets.values](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values)
collection, which provides direct cell read and write functionality. Other
aspects, such as adding sheets or updating sheet properties are handled by the
[spreadsheets](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets) collection.
Note that the v4 API's JSON structures are not backward-compatible with the XML
structures used in v3.

For more information about the resources available in the Sheets
v4 API, see the [API Reference](https://developers.google.com/workspace/sheets/api/reference/rest).

### Notation and terms

The v3 API refers to sheets within a particular spreadsheet as "worksheets".
This is synonymous with the term "sheets" that the v4 API uses.

The APIs often require you to specify a [spreadsheet
ID](https://developers.google.com/workspace/sheets/api/guides/concepts#spreadsheet_id) of the spreadsheet you
are working with. They also often require the ID of the sheet being manipulated.
These values appear either as part of the API endpoint URL, as query parameters,
or as part of a request body. In this page, the placeholders <var translate="no">spreadsheetId</var>
and <var translate="no">sheetId</var> refer to the spreadsheet and sheet IDs, respectively.
When using the methods described on this page, substitute in the actual IDs in
these locations.

The v3 API also assigns an ID to rows retrieved using its
[list feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_row_data);
this is represented in this page by the <var translate="no">rowId</var> placeholder.

### Authorize requests

When your app runs, it asks users to grant certain permissions; the scopes that
you specify in your application determine which permissions it asks for.

### v3 API

The Sheets API v3 operates with a single authorization scope:

```
https://spreadsheets.google.com/feeds
```

which is an alias for

```
https://www.googleapis.com/auth/spreadsheets
```

Either scope format can be used.

### v4 API

The Sheets API v4 uses one or more of the following set of scopes:

```
https://www.googleapis.com/auth/spreadsheets.readonly
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/drive.readonly
https://www.googleapis.com/auth/drive
```

Use read-only scopes if the your application does not need to make edits to
a user's sheets or sheet properties. Use spreadsheets scopes instead of
Drive scopes if the application does not need general Drive access.

### Visibility

In older versions of the API, the term *visibility* is used to refer to the
availability of a given spreadsheet.

### v3 API

The Sheets API v3 expresses visibility directly in its endpoints. A `public`
spreadsheet has been "Published to the Web" and thus can be accessed by the
API without authorization, while a `private` spreadsheet does require
authentication. Visibility is specified in the endpoint after the
spreadsheet ID:

```
https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full
```

### v4 API

In the new Sheets API v4, there is no explicit declaration of visibility.
API calls are made using spreadsheet IDs. If the application does not have
permission to access specified spreadsheet, an error is returned. Otherwise
the call proceeds.

### Projection

The term *projection* is used by the Sheets API v3 to refer to the set of data
that is returned by a given API call---either all of it, or a fixed subset
defined within the API. The Sheets API v4 does not use projection; rather, it
allows you more control over what data is returned.

### v3 API

There are only two possible projection settings in the Sheets API v3. `full`
projection returns all the available information, whereas `basic` returns a
smaller, fixed subset of data (for the worksheets, list, and cells feeds).
Like visibility, the projection must be specified in the API endpoint
(after the visibility setting):

```
https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/public/basic
```

The smaller subset of data provided by the `basic` projection is valuable
for making code more efficient, but cannot be customized.

### v4 API

While the Sheets API v4 can return a full data set, it does not define fixed
subsets analogous to the `basic` visibility setting the Sheets API v3.
Methods in the [spreadsheet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets)
collection restrict the amount of data they return through the use of
a [fields](https://developers.google.com/workspace/sheets/api/guides/concepts#partial_responses) query parameter.

For example, the following query only returns the titles of all the
sheets in a particular spreadsheet:

```
GET https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId?fields=sheets.properties.title
```

### Create a spreadsheet

### v3 API

The Sheets API v3 does not provide a means to create new spreadsheets;
instead, the [Drive API Files.create](https://developers.google.com/workspace/drive/v3/reference/files/create)
method can be used to create new spreadsheet files. This requires the
application to declare the `https://www.googleapis.com/auth/drive` scope.

### v4 API

The [Drive API Files.create](https://developers.google.com/workspace/drive/v3/reference/files/create) method can
also be used with the Sheets API v4, but requires the application to provide
the `https://www.googleapis.com/auth/drive` scope.

As an equivalent alternative, the Sheets API v4 provides a
[spreadsheets.create](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/create)
method, which can also optionally add sheets, set the spreadsheet and sheet
properties, and add named ranges. For example, the following creates a new
spreadsheet and gives it the name "NewTitle":

```
POST https://sheets.googleapis.com/v4/spreadsheets
```

```
{
 "properties": {"title": "NewTitle"}
}
```

### List spreadsheets for the authenticated user

### v3 API

The Sheets API v3 feed allows an application to retrieve a list of all the
spreadsheets accessible by the authenticated user. The spreadsheet feed
endpoint is:

```
GET https://spreadsheets.google.com/feeds/spreadsheets/private/full
```

### v4 API

The Sheets API v4 does not provide this specific operation. We recommend
migrating your app to use the drive.file scope in combination with the
Google Picker for spreadsheet selection.

In the cases where listing spreadsheets is required, it can be replicated
via the [Drive API Files.list](https://developers.google.com/workspace/drive/v3/reference/files/list) method, using
a `mimeType` query:

```
GET https://www.googleapis.com/drive/v3/files
             ?q=mimeType='application/vnd.google-apps.spreadsheet'
```

Using the Drive API files.list method to list all of a user's spreadsheets
requires a [restricted](https://developers.google.com/workspace/drive/api/v3/about-auth#OAuth2Authorizing) scope.

### Retrieve sheet metadata

The Sheets API v3 provides a feed to access the sheet metadata
contained within a given spreadsheet (row and cell data is accessed through
a separate feed). The metadata includes information such as sheet titles and
size information.

The Sheets API v4
[spreadsheets.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
method provides access to this information, and much more.

### v3 API

The worksheet feed is accessible from this API endpoint (using an
appropriate authorization header):

```
GET https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full
```

The response to this request has a structure similar to this, with
each sheet's data contained in a separate `<entry>`:

    <feed xmlns="http://www.w3.org/2005/Atom"
        xmlns:openSearch="http://a9.com/-/spec/opensearch/1.1/"
        xmlns:gs="http://schemas.google.com/spreadsheets/2006"
        xmlns:gd="http://schemas.google.com/g/2005"
        gd:etag='W/"D0cERnk-eip7ImA9WBBXGEg."'>
      <id>https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full</id>
      <updated>2006-11-17T18:23:45.173Z</updated>
      <title type="text">Groceries R Us</title>
      <link rel="alternate" type="text/html"
          href="https://spreadsheets.google.com/ccc?key=spreadsheetId"/>
      <link rel="http://schemas.google.com/g/2005#feed"
          type="application/atom+xml"
          href="https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full"/>
      <link rel="self" type="application/atom+xml"
          href="https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full"/>
      <link rel="http://schemas.google.com/g/2005#post" type="application/atom+xml"
          href="https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full"/>
      <author>
        <name>Fitzwilliam Darcy</name>
        <email>fitz@example.com</email>
      </author>
      <openSearch:totalResults>1</openSearch:totalResults>
      <openSearch:startIndex>1</openSearch:startIndex>
      <openSearch:itemsPerPage>1</openSearch:itemsPerPage>
      <entry gd:etag='"YDwqeyI."'>
        <id>https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId</id>
        <updated>2006-11-17T18:23:45.173Z</updated>
        <title type="text">Sheet1</title>
        <content type="text">Sheet1</content>
        <link rel="http://schemas.google.com/spreadsheets/2006#listfeed"
            type="application/atom+xml"
            href="https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full"/>
        <link rel="http://schemas.google.com/spreadsheets/2006#cellsfeed"
            type="application/atom+xml"
            href="https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full"/>
        <link rel="self" type="application/atom+xml"
            href="https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId"/>
        <link rel="edit" type="application/atom+xml"
            href="https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId/version"/>
        <gs:rowCount>100</gs:rowCount>
        <gs:colCount>20</gs:colCount>
      </entry>
    </feed>

### v4 API

The [spreadsheets.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
method can be used to acquire sheet properties and other metadata---much
more than what is available using the Sheets API v3. If you only
want to read the sheet properties, set the `includeGridData` query
parameter to `false` to prevent the inclusion of the spreadsheet cell data:

```
GET https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId?includeGridData=false
```

The [`Spreadsheet`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#resource-spreadsheet)
response contains an array of [`Sheet`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#sheet)
objects; the sheet titles and size information specifically can be found
under the [`SheetProperties`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#sheetproperties)
element of these objects. For example:

```
{
  "spreadsheetId": spreadsheetId,
  "sheets": [
      {"properties": {
          "sheetId": sheetId,
          "title": "Sheet1",
          "index": 0,
          "gridProperties": {
              "rowCount": 100,
              "columnCount": 20,
              "frozenRowCount": 1,
              "frozenColumnCount": 0,
              "hideGridlines": false
          },
          ...
       },
       ...
      },
      ...
  ],
  ...
}
```

### Add a sheet to a spreadsheet

Both APIs allow you to add new sheets to an existing spreadsheet.

### v3 API

The Sheets API v3 can add new worksheets to a spreadsheet by making the
following (authenticated) `POST` request. You can specify the size of the
new sheet:

```
POST https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full
```

    <entry xmlns="http://www.w3.org/2005/Atom"
        xmlns:gs="http://schemas.google.com/spreadsheets/2006">
      <title>Expenses</title>
      <gs:rowCount>50</gs:rowCount>
      <gs:colCount>10</gs:colCount>
    </entry>

### v4 API

You can add new sheets by making an
[AddSheet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#addsheetrequest)
request in the [spreadsheets.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method. As part of request body, you can specify the sheet properties for
the new sheet; all properties are optional. It is an error to provide a
title that is used for an existing sheet.

```
POST https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId:batchUpdate
```

```
{
  "requests": [{
      "addSheet": {
          "properties": {
            "title": "Expenses",
            "sheetType": "GRID",
            "gridProperties": {
              "rowCount": 50,
              "columnCount": 10
            }
          }
      }
  }],
}
```

### Change a sheet title and size

The Sheets API v3 lets you update sheet titles and size; the
Sheets API v4 allows this as well, but can also be used to update
other sheet properties. Note that reducing a sheet's size may cause data in the
cropped cells to be deleted without warning.

### v3 API

To change the title or size of a worksheet, begin by retrieving the
[worksheet feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_sheet_metadata) and
finding the desired worksheet entry, which contains an `edit` URL.
Update the worksheet's metadata and send it as the body of a `PUT` request
to the edit URL. For example:

```
PUT https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId/version
```

    <entry>
      <id>
        https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId
      </id>
      <updated>2007-07-30T18:51:30.666Z</updated>
      <category scheme="http://schemas.google.com/spreadsheets/2006"
        term="http://schemas.google.com/spreadsheets/2006#worksheet"/>
      <title type="text">Expenses</title>
      <content type="text">Expenses</content>
      <link rel="http://schemas.google.com/spreadsheets/2006#listfeed"
        type="application/atom+xml" href="https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full"/>
      <link rel="http://schemas.google.com/spreadsheets/2006#cellsfeed"
        type="application/atom+xml" href="https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full"/>
      <link rel="self" type="application/atom+xml"
        href="https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId"/>
      <link rel="edit" type="application/atom+xml"
        href="https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId/version"/>
      <gs:rowCount>45</gs:rowCount>
      <gs:colCount>15</gs:colCount>
    </entry>

### v4 API

To update the size, title, and other sheet properties, make an
[updateSheetProperties](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#updatespreadsheetpropertiesrequest)
request in the
[spreadsheets.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method. The `POST` request body should contain the properties to be
changed, and the `fields` parameter should explicitly list those properties
(if you want to update all properties, use `fields:"*"` as a shorthand for
listing them all). For
example, the following specifies that the sheet title and size
properties should be updated for the sheet with the given ID:

```
POST https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId:batchUpdate
```

```
{
  "requests": [
    {
      "updateSheetProperties": {
          "properties": {
            "sheetId": sheetId,
            "title": "Expenses",
            "gridProperties": {
              "rowCount": 45,
              "columnCount": 15,
            }
          },
          "fields": "title,gridProperties(rowCount,columnCount)"
     }
   }
  ],
}
```

To retrieve a sheet's <var translate="no">sheetId</var>, use the spreadsheet
[spreadsheets.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get) method.

### Delete a sheet

Either API can remove sheets from a given spreadsheet.

### v3 API

To delete a worksheet, begin by retrieving the
[worksheet feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_sheet_metadata), then
send a `DELETE` request on the `edit` URL of the target worksheet entry.

```
DELETE https://spreadsheets.google.com/feeds/worksheets/spreadsheetId/private/full/sheetId/version
```

### v4 API

To delete a sheet, make a
[DeleteSheet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#deletesheetrequest)
request in the
[spreadsheets.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method. The `POST` request body should only contain the <var translate="no">sheetId</var> for the
sheet to be deleted. For example:

```
POST https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId:batchUpdate
```

```
{
  "requests": [
    {
      "deleteSheet": {
        "sheetId": sheetId
      }
    }
  ],
}
```

To retrieve an individual sheet's <var translate="no">sheetId</var>, use the
spreadsheet [spreadsheets.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
method.

### Retrieve row data

The *list rows feed* is one of the two methods the Sheets API v3 provides to
access data within a spreadsheet's cells (the other being the *cells feed*). The
rows feed is meant to support common spreadsheet operations (reading row by row,
appending rows, sorting), but makes certain assumptions that make it unsuitable
for some tasks. Specifically, the list feed assumes that blank rows are feed
terminations, and that mandatory headers are present in the first row of a
sheet.

In contrast, the Sheets API v4 does not use access methods that are
row-specific. Instead, sheet cell data is accessed by referencing the specific
ranges required using [A1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#a1_notation). The
ranges can be blocks of cells, entire rows, entire columns, or entire sheets.
The API can also access disjoint sets of cells.

### v3 API


To determine the URL of a list-based feed for a given worksheet, retrieve
the [worksheet feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_sheet_metadata) and
find the list feed URL in the worksheet entry of interest.

To retrieve a list-based feed, send a `GET` request to the list feed URL,
using an appropriate authorization header. For example:

```
GET https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full
```

The response to this request contain, among other things, entries
corresponding to specific rows. Individual cells are referenced by
the names provided in the (mandatory) sheet header row. For example, here
is a single row entry:

    <entry gd:etag='"S0wCTlpIIip7ImA0X0QI"'>
      <id>rowId</id>
      <updated>2006-11-17T18:23:45.173Z</updated>
      <category scheme="http://schemas.google.com/spreadsheets/2006"
          term="http://schemas.google.com/spreadsheets/2006#list"/>
      <title type="text">Bingley</title>
      <content type="text">Hours: 10, Items: 2, IPM: 0.0033</content>
      <link rel="self" type="application/atom+xml"
          href="https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full/rowId"/>
      <link rel="edit" type="application/atom+xml"
          href="https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full/rowId/version"/>
      <gsx:name>Bingley</gsx:name>
      <gsx:hours>10</gsx:hours>
      <gsx:items>2</gsx:items>
      <gsx:ipm>0.0033</gsx:ipm>
    </entry>

By default the rows returned in the list feed are returned in row order.
The Sheets API v3 provides query parameters to change that order.

Reverse order:

```
GET https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full?reverse=true
```

Order by a specific column:

```
GET https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full
             ?orderby=column:lastname
```

The Sheets API v3 also allows filtering of specific rows via a structured
query (referenced by column headers):

```
GET https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full
             ?sq=age>25%20and%20height<175
```

### v4 API

With the Sheets API v4, rows can be retrieved by range using the
[spreadsheets.values.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get)
or [spreadsheets.values.batchGet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet)
methods. For example, the following returns all rows in "Sheet1":

```
GET https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId/values/Sheet1
```

The response to this request has a structure similar to:

```
{
  "range": "Sheet1",
  "majorDimension": "ROWS",
  "values": [["Name", "Hours", "Items", "IPM"],
             ["Bingley", "10", "2", "0.0033"],
             ["Darcy", "14", "6", "0.0071"]]
}
```

Trailing empty cells are not included in the response when retrieving entire
rows, columns, or sheets.

The Sheets API v4 does not have equivalents for the row-order query
parameters provided by the Sheets API v3. Reverse-order is trivial; simply
process the returned `values` array in reverse order. Order by column is not
supported for reads, but it is possible to sort the data in the sheet (using
a [SortRange](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#sortrangerequest))
request and then read it.

The Sheets API v4 does not currently have a direct equivalent for the Sheets API v3 structured queries. However, you can retrieve the relevant data and
sort through it as needed in your application.

### Add a new row of data

You can add a new row of data to a sheet using either API.

### v3 API

To determine the URL of a list-based feed for a given worksheet, retrieve
the [worksheet feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_sheet_metadata) and
find the post URL in the worksheet entry of interest.

To add a row of data, send a `POST` request to the post URL,
using an appropriate authorization header. For example:

```
POST https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full
```

The body of the `POST` request should contain an entry for the row data to
add, with individual cells referenced by column headers:

    <entry xmlns="http://www.w3.org/2005/Atom"
           xmlns:gsx="http://schemas.google.com/spreadsheets/2006/extended">
      <gsx:hours>2</gsx:hours>
      <gsx:ipm>0.5</gsx:ipm>
      <gsx:items>60</gsx:items>
      <gsx:name>Elizabeth</gsx:name>
    </entry>

New rows are appended to the end of the specified sheet.

### v4 API

With the Sheets API v4, you can append rows using the
[spreadsheets.values.append](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append)
method. The following example writes a new row of data below the last
table in "Sheet1" of a spreadsheet.

```
POST https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId/append/Sheet1
```

<br />

```
{
   "values": [["Elizabeth", "2", "0.5", "60"]]
}
```

Additionally, the Sheets API v4 also lets you append cells with specific
properties and formatting using the
[AppendCells](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#appendcellsrequest)
requests in a
[spreadsheets.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).

### Edit a row with new data

Both APIs allow row data to be updated with new values.

### v3 API

To edit a row of data, examine the [list feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_row_data)
to locate the entry for the row you wish to update. Update the contents of
that entry as needed. Be sure that the ID value in the entry you use exactly
matches the ID of the existing entry.

Once the entry has been updated, send a `PUT` request with the entry as the
request body to the `edit` URL provided in that row entry,
using an appropriate authorization header. For example:

```
PUT https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full/rowId/version
```

    <entry gd:etag='"S0wCTlpIIip7ImA0X0QI"'>
      <id>rowId</id>
      <updated>2006-11-17T18:23:45.173Z</updated>
      <category scheme="http://schemas.google.com/spreadsheets/2006"
        term="http://schemas.google.com/spreadsheets/2006#list"/>
      <title type="text">Bingley</title>
      <content type="text">Hours: 10, Items: 2, IPM: 0.0033</content>
      <link rel="self" type="application/atom+xml"
        href="https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full/rowId"/>
      <link rel="edit" type="application/atom+xml"
        href="https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full/rowId/version"/>
      <gsx:name>Bingley</gsx:name>
      <gsx:hours>20</gsx:hours>
      <gsx:items>4</gsx:items>
      <gsx:ipm>0.0033</gsx:ipm>
    </entry>

### v4 API


With Sheets API v4, you can edit a row using the
[A1 notation](https://developers.google.com/workspace/sheets/api/guides/concepts#a1_notation) of the row you wish to
edit and issuing a
[spreadsheets.values.update](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update)
request to overwrite that row. The range specified need only refer to the
first cell in the row; the API infers the cells to update based on the
values provided with the request. If you instead specify a multi-cell range,
the values you provide must fit within that range; if not the API returns an
error.

The following example request and request body adds data to the
fourth row of "Sheet1":

```
PUT https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId/values/Sheet1!A4
```

```
{
   "values": [["Elizabeth", "2", "0.5", "60"]]
}
```

You can also update row data from the
[spreadsheet.values.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)
method; it is more efficient to use this method if you are making multiple
row or cell updates.

Additionally, the Sheets API v4 also lets you edit the cell properties and
formatting of cells using the
[UpdateCells](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#updatecellsrequest)
or
[RepeatCell](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#repeatcellrequest)
requests in a
[spreadsheets.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).

### Delete a row

Both APIs support the deletion of rows. A deleted row is removed from the
spreadsheet, and rows below it are pushed up one.

### v3 API

To delete a row, first retrieve the row to delete from the
[list feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_row_data),
then send a `DELETE` request to the `edit` URL provided in the row's entry.
This is the same URL used to update the row.

```
DELETE https://spreadsheets.google.com/feeds/list/spreadsheetId/sheetId/private/full/rowId/version
```

If you want to make sure that you don't delete a row that has been changed
by another client since you retrieved it, include an HTTP If-Match header
that contains the original row's ETag value. You can determine the original
row's ETag value by examining the entry element's gd:etag attribute.

If you want to delete the row regardless of whether someone else has updated
it since you retrieved it, then use If-Match: \* and don't include the ETag.
(In this case, you don't need to retrieve the row before deleting it.)

### v4 API

Deleting rows with the Sheets API v4 is handled by a [spreadsheet.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
method call, using a [DeleteDimension](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate#deletedimensionrequest)
request. This request can also be used to remove columns, and developers
and choose to only remove part of a row or column. For example, the
following removes the 6th row of a sheet with the given ID (the row indices
are zero-based, with the startIndex inclusive and endIndex exclusive):

```
POST https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId:batchUpdate
```

```
{
  "requests": [
    {
      "deleteDimension": {
        "range": {
          "sheetId": sheetId,
          "dimension": "ROWS",
          "startIndex": 5,
          "endIndex": 6
        }
      }
    }
  ],
}
```

A sheet's <var translate="no">sheetId</var> can be retrieved using the
[spreadsheet.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get) method.

### Retrieve cell data

The Sheets API v3 provides a cell feed for basic access to all data stored in a
spreadsheet. For read access, the cell feed can provide the entire sheet
content or a range of the sheet's cells defined by a set of query parameters,
but only as a single block---disjoint ranges have to be retrieved
separately using additional `GET` requests.

The Sheets API v4 can retrieve any set of cell data from a sheet (including
multiple disjoint ranges). The Sheets API v3 can only return cell contents as
input values (as would be entered by a user at a keyboard) and/or the outputs of
formula (if numeric); the Sheets API v4 grants full access to values,
formulas, formatting, hyperlinks, data validation, and other properties.

### v3 API

To determine the URL of a cell-based feed for a given worksheet, examine the
[worksheet feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_sheet_metadata)
and find the cell feed URL in the worksheet entry of interest.

To retrieve a cell-based feed, send a `GET` request to the cell feed URL,
using an appropriate authorization header. For example:

```
GET https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full
```

Cells are referenced using row and column number. Fetching a single specific
range can be done by using `max-row`, `min-row`, `max-col` and `min-col`
query parameters. For example, the following retrieves all cells in column
4 (D), starting with row 2:

```
GET https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full
             ?min-row=2&min-col=4&max-col=4
```

The Sheets API v3 returns the `inputValue` of retrieved cells---the
value that a user would otherwise type into the Google Sheets user
interface to manipulate the cell. The `inputValue` can be a literal value
or a formula. The API also sometimes returns a `numericValue`; for example,
when a formula results in a number. For example, a response may include cell
entries similar in structure to the following:

    <entry gd:etag='"ImB5CBYSRCp7"'>
      <id>https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R9C4</id>
      <updated>2006-11-17T18:27:32.543Z</updated>
      <category scheme="http://schemas.google.com/spreadsheets/2006"
        term="http://schemas.google.com/spreadsheets/2006#cell"/>
      <title type="text">D4</title>
      <content type="text">5</content>
      <link rel="self" type="application/atom+xml"
        href="https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R9C4"/>
      <link rel="edit" type="application/atom+xml"
        href="https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R9C4/srevc"/>
      <gs:cell row="4" col="4" inputValue="=FLOOR(C4/(B4*60),.0001)"
        numericValue="5.0">5</gs:cell>
    </entry>

### v4 API

Retrieve cell data by calling a
[spreadsheets.values.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get)
or [spreadsheets.values.batchGet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet)
method for the range or ranges of interest, respectively. For example, the
following returns the cells in column D of "Sheet2", starting with row 2,
in column-major order and returning formulas as entered (trailing empty
cells are omitted):

```
GET https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId/values/Sheet2!D2:D?majorDimension=COLUMNS&valueRenderOption=FORMULA
```

The response to this request is similar in structure to:

```
{
  "spreadsheetId": spreadsheetId,
  "valueRanges": [
      {"range": "Sheet2!D2:D",
       "majorDimension": "COLUMNS",
       "values": [["Widget", 234, "=FLOOR(C4/(B4*60),.0001)", "=D4\*1000"]]
      }]
}
```

It is more efficient to use
[spreadsheet.values.batchGet](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchGet)
if you intend to retrieve multiple ranges of cell data. If you want to
access cell properties such as formatting, the
[spreadsheet.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
method is required.

### Edit a cell

The Sheets API v3 lets you edit cell content by issuing a `PUT` command to
the cell feed with the modified cell entry as the request body.

The Sheets API v4, in contrast, provides
[spreadsheets.values.update](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update)
and
[spreadsheets.values.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)
methods for changing cell content.

### v3 API

To edit a single cell's content, first find the cell's entry in the
[cell feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_cell_data).
The entry contains an edit URL. Update the entry to reflect the contents
you want the cell to have, and then issue a `PUT` request to the edit url
with the updated cell entry as the body of the request. For example, the
following updates cell D2 (R2C4) to contain a `SUM` formula:

<br />

```
PUT https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full//R2C4/srevc
`<entry xmlns="http://www.w3.org/2005/Atom"
    xmlns:gs="http://schemas.google.com/spreadsheets/2006">
  <id>https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R2C4</id>
  <link rel="edit" type="application/atom+xml"
    href="https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R2C4"/>
  <gs:cell row="2" col="4" inputValue="=SUM(A1:B6)"/>
</entry>
`
```

### v4 API

Single cell editing in the Sheets API v4 can be done with the
[spreadsheets.values.update](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/update)
method. This method requires a `ValueInputOption` query parameter, which
specifies whether the input data is treated as if entered into the
Sheets UI (`USER_ENTERED`), or left unparsed and taken as is (`RAW`). For
example, the following updates cell D2 with a formula:

```
PUT https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId/values/D2?valueInputOption=USER_ENTERED
```

```
{"values": [["=SUM(A1:B6)"]]}
```

If you are making multiple cell edits, use the
[spreadsheets.values.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)
method to issue them in one request.

### Edit multiple cells via batch request

Both APIs provide the means to make changes to the content of multiple cells
with a single (batch) request. The cells referred to by a batch request are
not required to be in a contiguous range.

In the case where one or more of the cell edits in the batch fail, the Sheets API v3 allows others to succeed. However, the Sheets API v4 returns an error
if any of the batched updates fail, and does not apply any of them in that case.

### v3 API

To edit multiple cells, first retrieve a [cell feed](https://developers.google.com/workspace/sheets/api/guides/migration#retrieve_cell_data)
for the worksheet. The entry contains a batch URL. Send a `POST`
request to this URL, along with a request body describing the cells you
wish to update and the new cell content. The `POST` request and request body
have a structure similar to the following:

```
POST https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/batch
```

    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:batch="http://schemas.google.com/gdata/batch"
          xmlns:gs="http://schemas.google.com/spreadsheets/2006">
      <id>https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full</id>
      <entry>
        <batch:id>request1</batch:id>
        <batch:operation type="update"/>
        <id>https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R2C4</id>
        <link rel="edit" type="application/atom+xml"
          href="https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R2C4/version"/>
        <gs:cell row="2" col="4" inputValue="newData"/>
      </entry>
      ...
      <entry>
        <batch:id>request2</batch:id>
        <batch:operation type="update"/>
        <id>https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R2C5</id>
        <link rel="edit" type="application/atom+xml"
          href="https://spreadsheets.google.com/feeds/cells/spreadsheetId/sheetId/private/full/R2C5/version"/>
        <gs:cell row="5" col="2" inputValue="moreInfo"/>
      </entry>
    </feed>

The `batch:id` field should uniquely identify the request within the batch.
The `batch:operation` field should be `update` for cell edits. `gs:cell`
identifies the cell by row and column number and provides the new data
to insert there. `id` contains the full URL to the cell to be updated.
`link` must have a `href` attribute that contains the full path to the
cell's ID. All of these fields are required for each entry.

### v4 API

The Sheets API v4 provides batch editing of cell values through the
[spreadsheets.values.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)
method.

Editing multiple cells can be done by issuing a `POST` request with the
data changes specified in the request body. For example:

```
POST https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId/values:batchUpdate
```

```
{
  "valueInputOption": "USER_ENTERED"
  "data": [
       {"range": "D4",
        "majorDimension": "ROWS",
        "values": [["newData"]]
       },
       {"range": "B5",
        "majorDimension": "ROWS",
        "values": [["moreInfo"]]
       }
  ]
}
```

If you specified a single cell as the range, all values provided are
written to the sheet starting with that cell as the upper-left coordinate.
If you instead specify a multi-cell range, the values you provide must fit
that range exactly; if not the API returns an error.


# Use field masks

Field masks are a way for API callers to list the fields that a request should
return or update. Using a
[FieldMask](https://protobuf.dev/reference/protobuf/google.protobuf/#field-mask)
allows the API to avoid unnecessary work and improves performance. A field mask
is used for both the read and update methods in the Google Sheets API.

## Read with a field mask

[Video](https://www.youtube.com/watch?v=T1FUDXRB7Ns)

Spreadsheets can be large, and often you don't need every part of the
[`Spreadsheet`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#resource:-spreadsheet)
resource returned by a read request. You can limit what's returned in a
Sheets API response, using the `fields` URL parameter. For best
performance, [explicitly list only the fields you
need](https://developers.google.com/workspace/sheets/api/guides/performance#partial-response) in the reply.

The format of the fields parameter is the same as the [JSON encoding of a
FieldMask](https://protobuf.dev/reference/protobuf/google.protobuf/#json-encoding-field-masks).
Stated briefly, multiple different fields are comma-separated and subfields are
dot-separated. Field names can be specified in **camelCase** or
**separated_by_underscores**. For convenience, multiple subfields from the same
type can be listed within parentheses.

The following
[`spreadsheets.get`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get)
request example uses a field mask of
`sheets.properties(sheetId,title,sheetType,gridProperties)` to fetch only the
sheet ID, title,
[`SheetType`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#SheetType),
and
[`GridProperties`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#GridProperties)
of a
[`SheetProperties`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#SheetProperties)
object on all sheets in a spreadsheet:

```
GET https://sheets.googleapis.com/v4/spreadsheets/spreadsheetId?fields=sheets.properties(sheetId,title,sheetType,gridProperties)
```

The response to this method call is a
[`Spreadsheet`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets#resource:-spreadsheet)
object containing the components requested in the field mask. Note that
`sheetType=OBJECT` doesn't contain `gridProperties`:

    {
      "sheets": [
        {
          "properties": {
            "sheetId": SHEET_ID,
            "title": "TITLE",
            "sheetType": "GRID",
            "gridProperties": {
              "rowCount": 1000,
              "columnCount": 25
            }
          }
        },
        {
          "properties": {
            "sheetId": SHEET_ID,
            "title": "TITLE",
            "sheetType": "OBJECT"
          }
        }
      ]
    }

## Update with a field mask

[Video](https://www.youtube.com/watch?v=W421BNZA_74)

Sometimes you need to update only certain fields in an object while leaving the
other fields unchanged. Update requests inside a
[`spreadsheets.batchUpdate`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)
operation use field masks to tell the API which fields are being changed. The
update request ignores any fields that aren't specified in the field mask,
leaving them with their current values.

You can also unset a field by not specifying it in the updated message, but
adding the field to the mask. This clears whatever value the field previously
had.

The syntax for update field masks is the same as read field masks.

> [!NOTE]
> A field mask of `*` is treated like a wildcard and is shorthand for specifying every field in a message. The wildcard syntax can produce unwanted results if the API is updated in the future, as read-only fields and newly added fields may cause errors. For production applications, always list the specific fields being updated in field masks and avoid using `*` wildcards.

The following example uses the
[`AddSheetRequest`](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#addsheetrequest)
to add a new sheet of type `Grid`, freeze the first row, and color the new
sheet's tab red:

```
POST https://sheets.googleapis.com/v1/spreadsheets/spreadsheetId:batchUpdate
```

    {
      "spreadsheetId": "SPREADSHEET_ID",
      "replies": [
        {
          "addSheet": {
            "properties": {
              "sheetId": SHEET_ID,
              "title": "TITLE",
              "index": 6,
              "sheetType": "GRID",
              "gridProperties": {
                "rowCount": 1000,
                "columnCount": 26,
                "frozenRowCount": 1
              },
              "tabColor": {
                "red": 0.003921569
              },
              "tabColorStyle": {
                "rgbColor": {
                  "red": 0.003921569
                }
              }
            }
          }
        }
      ]
    }