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