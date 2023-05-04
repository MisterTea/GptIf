import { ArrowForwardIos } from '@mui/icons-material';
import { Grid } from '@mui/material';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CssBaseline from '@mui/material/CssBaseline';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import TextField from '@mui/material/TextField';
import { observer } from "mobx-react";
import { KeyboardEvent, useRef } from 'react';
//import rehypeRaw from 'rehype-raw';
//import remarkDirective from 'remark-directive';
//import remarkGfm from 'remark-gfm';
import { Marked } from '@ts-stack/markdown';
import './App.css';
import DataStore, { ChatBlock } from './datastore';
import logo from './logo.svg'; // Tell webpack this JS file uses this image

var API_SERVER_BASE: string = "/";
if (window.location.hostname.endsWith("amazonaws.com")) {
  API_SERVER_BASE = "https://90rjg2sbkg.execute-api.us-east-1.amazonaws.com/dev/";
}

const darkTheme = createTheme({
  palette: {
    mode: 'dark',
  },
});

const App = observer(({ datastore }: { datastore: DataStore }) => {
  const valueRef: React.MutableRefObject<any> = useRef('') //creating a refernce for TextField Component

  function createChatBlockFromResponse(responseResults: any) {
    const chatBlock = new ChatBlock();
    const chatSections = responseResults.map((responseResult: any[]) => {
      var responseText = responseResult[0];
      if (responseResult[1] !== null) {
        //const colorHtmlMap = new Map<string, string[]>();
        //colorHtmlMap.set("yellow", ["<span style=\"color:yellow\">", "</span>"]);
        //const htmlResult: string[] | undefined = colorHtmlMap.get(responseResult[1]);
        //if (htmlResult === undefined) {
        //throw "Oops: " + responseResult[1];
        //}
        responseText = "[" + responseResult[1] + "]" + responseText + "[/]";
      }

      // Replace rich tags with spans
      const acceptedTags = ["yellow", "blue", "bright_blue bold", "yellow bold", "purple"]
      responseText = responseText.replaceAll("[/]", "</span>")
      acceptedTags.forEach(acceptedTag => {
        responseText = responseText.replaceAll("[" + acceptedTag + "]", "<span class=\"game_markdown_" + acceptedTag.replaceAll(" ", "_") + "\">")
      });
      return Marked.parse(responseText);
    })
    chatBlock.chatSections = chatSections;
    return chatBlock;
  }

  function submit_command() {
    const command = valueRef.current.value;
    //if (command.length === 0)
    //return;
    valueRef.current.value = "";
    console.log("SUBMITTING");
    console.log(valueRef.current.value);
    fetch(API_SERVER_BASE + "api/handle_input", {
      method: "POST",
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ "command": command }),
      credentials: 'include',
    }).then(async (value: Response) => {
      console.log("GETTING BODY");
      const responseResults = await value.json();
      console.log(responseResults);
      const chatBlock = createChatBlockFromResponse(responseResults);
      datastore.addChatBlock(chatBlock);
    });
  }

  function submitIfEnter(e: KeyboardEvent) {
    if (e.key === "Enter") {
      submit_command();
    }
  }

  function submit_new_game() {
    console.log("STARTING NEW GAME");
    fetch(API_SERVER_BASE + "api/begin_game", {
      method: "POST",
      credentials: 'include',
    }).then(async (value: Response) => {
      console.log("GETTING BODY");
      const responseResults = await value.json();
      const chatBlock = createChatBlockFromResponse(responseResults);
      datastore.newGame(chatBlock);
    });
  }

  var counter = 0;
  const game_text = (<ul>
    {datastore.blocks.map(chatBlock => {
      counter += 1;
      return <div key={counter} dangerouslySetInnerHTML={{ __html: chatBlock.chatSections.join("\n\n") }}></div>
    })}
  </ul>
  );

  var commandBox = null;
  if (datastore.blocks.length > 0) {
    commandBox = (
      <Box sx={{ display: 'flex', p: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-end', flexGrow: 1 }}>
          <ArrowForwardIos sx={{ color: 'action.active', mr: 1, my: 0.5 }} />
          <TextField id="input-with-sx" label="Tap/Click here" variant="standard" fullWidth onKeyDown={submitIfEnter} inputRef={valueRef} />
        </Box>
        <Button variant="contained" onClick={submit_command}>Submit</Button>
      </Box>
    );
  }

  return (
    <ThemeProvider theme={darkTheme}>
      <CssBaseline />
      <div className="App">
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            {game_text}
            {commandBox}
          </Grid>
          <Grid item xs={12} md={6}>
            <img src={logo} alt="Logo" />
          </Grid>
          <Grid item xs={12}>
            <div>
              <Button variant="contained" onClick={submit_new_game}>{datastore.blocks.length === 0 ? "Start Game" : "Restart Game"}</Button>
            </div>
          </Grid>
        </Grid>
      </div>
    </ThemeProvider>

  );
});

export default App;
