# The two opening questions

For an open ask ("help me create a strategy", "set me up", "what should I trade?") with no thesis yet. Ask them
in this order, one per call, and the second only after the first is answered.

When the `ask_user_question` tool is offered, ask through it. The app shows each option as a numbered choice, with
the `label` as its title and the `description` as a one-line subtitle, and "Or say something else" when
`allow_text` is true. Always set `allow_text: true`. Never add an "Other" option: the text field is that choice.

## 1. What matters most to you?

| id | label | description |
|---|---|---|
| `steady_growth` | Steady growth | Fewer trades, smaller swings |
| `big_moves` | Catch big moves | Bigger swings, higher upside |
| `any_market` | Earn in any market | Long or short, follows the trend |

## 2. How hands-on do you want to be?

| id | label | description |
|---|---|---|
| `hands_off` | Hands-off | I pick the details and tell you what I chose |
| `guided` | Guide me | I suggest, you confirm each step |
| `hands_on` | Hands-on | You decide every setting |

## Reading the answers

- A typed answer ("something lower risk") counts the same as a tapped one.
- The first answer sets the risk appetite and the shape. Steady growth means smaller swings and fewer trades. Catch
  big moves means wider exits and fewer, larger trades. Earn in any market allows both long and short.
- The second answer sets how many of the build decisions you ask and how many you propose.
- Then mine the answers as the thesis and build it with them, as the skill describes; a template only if they want one.

## Without the tool

In chats where `ask_user_question` is not offered (Telegram, connectors), ask the same two questions in plain text,
one at a time, with the options as a short list. The rest of the flow does not change.
