from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from enum import IntEnum
from io import StringIO
from typing import Dict, List, Optional, Set, Tuple, cast

import dice
import jinja2
import yaml
from md2py import TreeOfContents, md2py
from rich.console import Console
from rich.markdown import Markdown

import gptif.settings
from gptif.cl_image import display_image_for_prompt
from gptif.console import console
from gptif.db import GameState

try:
    from yaml import CDumper as Dumper
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader, Dumper

from gptif.parser import get_hypernyms_set, get_verb_classes, get_verb_classes_for_list


class Gender(IntEnum):
    MALE = 1
    FEMALE = 2


@dataclass
class AgentProfile:
    name: str
    age: Optional[int]
    race: str
    gender: Optional[Gender]
    occupation: Optional[str]
    personality: Optional[List[str]]
    backstory: Optional[List[str]]
    appearance: Optional[List[str]]
    hobbies: Optional[List[str]]
    goals: Optional[List[str]]

    @classmethod
    def load_yaml(cls, profile_yaml):
        return AgentProfile(
            profile_yaml["name"],
            profile_yaml["age"],
            profile_yaml.get("race", "Caucasian"),
            profile_yaml["gender"],
            profile_yaml["occupation"],
            profile_yaml["personality"],
            profile_yaml["backstory"],
            profile_yaml["physical appearance"],
            profile_yaml["hobbies"],
            profile_yaml["goals"],
        )

    def init_player_visible(self) -> AgentProfile:
        return AgentProfile(
            self.name,
            self.age,
            self.race,
            self.gender,
            None,
            None,
            None,
            self.appearance,
            None,
            None,
        )


@dataclass
class Agent:
    uid: str
    profile: AgentProfile
    percent_increase_per_tic: str
    tic_creatives: List[str]
    friend_questions: List[str]
    notes: List[str]
    aliases: List[str]
    movement: Movement

    room_id: Optional[str]
    tic_percentage: int = 0
    friend_points: int = 0

    @classmethod
    def load_yaml(cls, uid, agent_yaml):
        profile = AgentProfile.load_yaml(agent_yaml["Profile"])
        if "Tics" in agent_yaml:
            percent_increase_per_tic = agent_yaml["Tics"]["percent_increase_per_tick"]
            tic_creatives = [str(x) for x in agent_yaml["Tics"]["creative"]]
        else:
            percent_increase_per_tic = "0d1t"
            tic_creatives = []
        movement = Movement.from_yaml(agent_yaml["movement"])

        return cls(
            uid,
            profile,
            percent_increase_per_tic,
            tic_creatives,
            agent_yaml["friend_questions"] if "friend_questions" in agent_yaml else [],
            agent_yaml.get("notes", []),
            agent_yaml.get("aliases", []),
            movement,
            movement.starting_room,
        )

    @property
    def names(self) -> Set[str]:
        if self.profile.name is not None:
            return set(
                self.aliases + [self.profile.name, self.profile.name.split(" ")[0]]
            )
        return set(self.aliases)

    @property
    def name(self) -> str:
        return self.profile.name

    def answers_to_name(self, name: str) -> bool:
        return name.lower() in [x.lower() for x in self.names]


@dataclass
class Scenery:
    uid: str
    hints: Set[str]
    room_scope: Optional[Set[str]]
    names: Set[str]
    actions: Dict[str, List[str]]

    @property
    def nouns(self):
        return [x.strip() for x in id.split("/")]


@dataclass
class Exit:
    room_uid: str
    visible: Optional[str]
    prescript: Optional[str]
    postscript: Optional[str]


@dataclass
class Room:
    uid: str
    title: str
    descriptions: Dict[str, List[str]]
    scenery: List[Scenery] = field(default_factory=lambda: [])
    exits: Dict[str, Exit] = field(default_factory=dict)


world: World = None  # type: ignore


@dataclass
class World:
    rooms: Dict[str, Room] = field(default_factory=lambda: {})
    agents: Dict[str, Agent] = field(default_factory=lambda: {})

    waiting_for_player: bool = True
    active_agents: Set[str] = field(default_factory=set)
    current_room_id: str = ""
    time_in_room: int = 0
    visited_rooms: Set[str] = field(default_factory=set)
    on_chapter: int = 0
    time_in_chapter: int = 0
    inventory: List[str] = field(default_factory=list)
    game_over: bool = False
    password_letters_found: Set[str] = field(default_factory=set)

    version: int = 3

    random: random.Random = field(default_factory=lambda: random.Random(1))

    def __post_init__(self):
        global world
        world = self

        # Load room descriptions
        room_descriptions: Dict[str, Dict[str, List[str]]] = {}

        current_room = ""
        current_topic = ""
        with open("data/rooms/room_descriptions.md", "r") as fp:
            sections = fp.read().split("\n\n")
            for section in sections:
                if section[:2] == "##":
                    current_topic = section[2:].strip()
                    room_descriptions[current_room][current_topic] = []
                elif section[:1] == "#":
                    current_room = section[1:].strip()
                    room_descriptions[current_room] = {}
                else:
                    assert current_room != ""
                    assert current_topic != ""
                    room_descriptions[current_room][current_topic].append(section)

            # Re-split room descriptions
            for rd in room_descriptions.values():
                for topic in rd.keys():
                    rd[topic] = "\n\n".join(rd[topic]).split("{{< pagebreak >}}")

        # Load rooms
        with open("data/rooms/rooms.yaml", "r") as rooms_file:
            rooms_yaml = yaml.safe_load(rooms_file)
            for room_uid, room_yaml in rooms_yaml.items():
                assert room_uid not in self.rooms, f"Duplicate room_uid, {room_uid}"
                room_title = room_yaml["title"]
                exits = {}
                if "exits" in room_yaml:
                    for exit_direction, exit in room_yaml["exits"].items():
                        exits[exit_direction] = Exit(
                            exit["room"],
                            exit.get("visible", None),
                            exit.get("prescript", None),
                            exit.get("postscript", None),
                        )
                self.rooms[room_uid] = Room(
                    room_uid, room_title, room_descriptions[room_uid], [], exits
                )

        # Load scenery descriptions
        scenery_actions: Dict[str, Dict[str, List[str]]] = {}

        current_scenery = ""
        current_action = ""
        with open("data/rooms/scenery_actions.md", "r") as fp:
            sections = fp.read().split("\n\n")
            for section in sections:
                if section[:2] == "##":
                    current_action = section[2:].strip()
                    scenery_actions[current_scenery][current_action] = []
                elif section[:1] == "#":
                    current_scenery = section[1:].strip()
                    scenery_actions[current_scenery] = {}
                else:
                    assert current_scenery != ""
                    assert current_action != ""
                    scenery_actions[current_scenery][current_action].append(section)

            # Re-split scenery descriptions
            for sd in scenery_actions.values():
                for action in sd.keys():
                    sd[action] = "\n\n".join(sd[action]).split("{{< pagebreak >}}")

        # Load scenery
        with open("data/rooms/scenery.yaml", "r") as fp:
            all_scenery_yaml = yaml.safe_load(fp)
            for scenery_uid, scenery_yaml in all_scenery_yaml.items():
                scenery = Scenery(
                    scenery_uid,
                    set(scenery_yaml["hints"]),
                    set(scenery_yaml["rooms"]),
                    set(scenery_yaml["names"]),
                    scenery_actions[scenery_uid],
                )
                # Attach scenery to all rooms listed
                for room_id in scenery_yaml["rooms"]:
                    self.rooms[room_id].scenery.append(scenery)

        # Adjust room descriptions based on scenery
        for room in self.rooms.values():
            for description_list in room.descriptions.values():
                for scenery in room.scenery:
                    for scenery_hint in scenery.hints:
                        for i, description in enumerate(description_list):
                            description_list[i] = re.sub(
                                f"({scenery_hint})",
                                "**\\1**",
                                description,
                                0,
                                re.MULTILINE | re.IGNORECASE,
                            )

        # Load agents
        with open("data/agents/agents.yaml", "r") as agent_file:
            all_agent_yaml = yaml.safe_load(agent_file)
            for agent_uid, agent_yaml in all_agent_yaml.items():
                self.agents[agent_uid] = Agent.load_yaml(agent_uid, agent_yaml)

        pass

    def save(self, game_state: GameState):
        world_state = {
            "waiting_for_player": self.waiting_for_player,
            "active_agents": list(sorted(self.active_agents)),
            "current_room_id": self.current_room_id,
            "time_in_room": self.time_in_room,
            "visited_rooms": list(sorted(self.visited_rooms)),
            "on_chapter": self.on_chapter,
            "time_in_chapter": self.time_in_chapter,
            "inventory": self.inventory,
            "version": self.version,
            "password_letters_found": list(sorted(self.password_letters_found)),
        }
        agent_states = {}
        for agent_id, agent in self.agents.items():
            agent_states[agent_id] = {
                "room_id": agent.room_id,
                "tic_percentage": agent.tic_percentage,
                "friend_points": agent.friend_points,
            }
        game_state.world_state = json.dumps(world_state)
        game_state.agent_states = json.dumps(agent_states)
        game_state.rng = json.dumps(self.random.getstate())
        game_state.version = str(self.version)

    def load(self, session: GameState) -> bool:
        if session.version != str(self.version):
            # Incompatible
            return False

        world_state = json.loads(session.world_state)
        for k1, v1 in world_state.items():
            assert hasattr(self, k1)
            setattr(self, k1, v1)

        # Convert lists back to sets
        self.active_agents = set(self.active_agents)
        self.visited_rooms = set(self.visited_rooms)
        self.password_letters_found = set(self.password_letters_found)

        agent_states = json.loads(session.agent_states)
        for agent_id, agent_state in agent_states.items():
            for k2, v2 in agent_state.items():
                assert hasattr(self.agents[agent_id], k2)
                setattr(self.agents[agent_id], k2, v2)

        def convert_to_tuple(l):
            return tuple(convert_to_tuple(x) for x in l) if type(l) is list else l

        self.random.setstate(convert_to_tuple(json.loads(session.rng)))

        return True

    def upgrade(self, newer_world: World):
        if self.version != newer_world.version:
            raise Exception("Can't load the saved game from a different version")
        # Not implemented
        # self.rooms = newer_world.rooms
        # for agent_uid in newer_world.agents.keys():
        #     if agent_uid not in self.agents:
        #         self.agents[agent_uid] = newer_world.agents[agent_uid]
        #     else:
        #         self.agents[agent_uid].upgrade(newer_world.agents[agent_uid])

    def ask_to_press_key(self):
        console.ask_to_press_key()

    @property
    def current_room(self):
        return self.rooms[self.current_room_id]

    def min_wait_duration(self) -> int:
        wait_duration: Optional[int] = None

        for description in self.current_room.descriptions.keys():
            if description.startswith("Tic"):
                tic_time = int(description.split(" ")[1])
                if self.time_in_room < tic_time:
                    time_to_wait = tic_time - self.time_in_room
                    if wait_duration is None or time_to_wait < wait_duration:
                        wait_duration = time_to_wait

        if wait_duration is None:
            return 1
        return wait_duration

    def step(self):
        console.step_mode = True
        try:
            self.time_in_room += 1
            self.time_in_chapter += 1
            for agent in self.agents.values():
                if (
                    agent.room_id == self.current_room_id
                    and len(agent.tic_creatives) > 0
                ):
                    assert agent.percent_increase_per_tic.endswith("t")
                    agent.tic_percentage += cast(
                        int,
                        dice.roll(agent.percent_increase_per_tic, random=self.random),
                    )
                    if agent.tic_percentage >= 100:
                        agent.tic_percentage = 0
                        # Pick a random tic
                        self.play_sections(
                            [random.choice(agent.tic_creatives)], "purple"
                        )
                agent.movement.step(agent)
            if f"Tic {self.time_in_room}" in self.current_room.descriptions:
                self.play_sections(
                    self.current_room.descriptions[f"Tic {self.time_in_room}"],
                    "purple",
                    insert_pauses=True,
                )

            if self.on_chapter == 4 and self.time_in_chapter == 7:
                self.play_sections(
                    """Terrus pushes past David, making the older gentleman stumble and fall to one knee.  You run over to help David up as June spins around to face the tour.

**June**: Pardon me, sir, but the tour is still ongoing!

**Terrus Black**: Apologies, but I have urgent matters to attend to.

Terrus walks through June as if she wasn't there.  June quickly steps to the side.
As Terrus exits down the stairs, you notice a surveillance earpiece in his right ear.
June is visibly upset but her professional instincts kick in and she smiles politely to the remainder of the group.

**June**: Please bear with me for one moment.  Thank you!

June pulls out a two-way radio and murmurs something unintelligible.

{{< pagebreak >}}

After a few moments, the short conversation is over and June turns back to face the tour.

**June**: Alright, let's hurry along then!  Please enjoy this area for a moment longer, then the tour will continue shortly.
    """.split(
                        "{{< pagebreak >}}"
                    ),
                    insert_pauses=True,
                )
                self.agents["mercenary"].room_id = None

            if self.on_chapter == 4 and self.time_in_chapter == 20:
                # Captain yell
                self.start_ch5()
        finally:
            console.step_mode = False

    def move_to(self, room_id):
        assert room_id in self.rooms
        self.current_room_id = room_id
        self.time_in_room = 0
        if room_id in self.visited_rooms:
            self.look_quickly()
        else:
            self.visited_rooms.add(room_id)
            self.look()

    def parse(self, text):
        result = jinja2.Environment().from_string(text).render(world=self)
        # Extract tokens
        tokens = []

        def replace_tokens(matchobj):
            tokens.append(matchobj.group(1))
            return ""

        result_without_tokens = re.sub(r"%%(.*?)%%", replace_tokens, result, 0)

        return result_without_tokens, tokens

    def go(self, direction):
        direction = direction.lower()
        if direction not in self.current_room.exits:
            console.warning("You can't go that way.")
            return False
        if self.on_chapter == 3 or self.on_chapter == 4:
            if self.current_room_id == self.agents["tour_guide"].room_id:
                tour_guide_name = self.agents["tour_guide"].name
                console.print(
                    f'{tour_guide_name} holds up a hand: "Please stay close to me until the tour is over.  Soak up the sights and sounds!  There will be plenty of time to go back and visit a spot we missed.  Thank you!"'
                )
                return False
        exit = self.current_room.exits[direction]
        if not self.exit_visible(exit):
            console.warning("You can't go that way.")
            return False
        if exit.prescript is not None:
            result_without_tokens, tokens = self.parse(exit.prescript)

            if len(result_without_tokens) > 0:
                self.play_sections(
                    result_without_tokens.split("{{< pagebreak >}}"), insert_pauses=True
                )
            if "False" in tokens:
                return False
        self.move_to(exit.room_uid)
        if exit.postscript is not None:
            self.parse(exit.postscript)
        return True

    def send_agent(self, agent: Agent, direction: str):
        assert agent.room_id is not None
        direction = direction.lower()
        agent_room = self.rooms[agent.room_id]
        assert direction in agent_room.exits
        if self.current_room_id == agent.room_id:
            console.print(f"{agent.name} walks {direction}.")
        self.move_agent(agent, self.rooms[agent_room.exits[direction].room_uid])
        if self.current_room_id == agent.room_id:
            console.print(f"{agent.name} walks in.")

    def move_agent(self, agent: Agent, room: Optional[Room]):
        if room is None:
            agent.room_id = None
        else:
            agent.room_id = room.uid

    def render_image(self, prompt: str):
        display_image_for_prompt(prompt)

    def look(self):
        if console.step_mode:
            console.step_mode = False
            console.ask_to_press_key()

        self.print_header()
        self.play_sections(self.current_room.descriptions["Long"], markdown=True)
        display_image_for_prompt(
            self.current_room.descriptions["Long"][0].split("\n\n")[0]
        )
        self.print_footer()

    def look_quickly(self):
        if console.step_mode:
            console.step_mode = False
            console.ask_to_press_key()

        self.print_header()
        self.play_sections(self.current_room.descriptions["Short"], markdown=True)
        display_image_for_prompt(
            self.current_room.descriptions["Long"][0].split("\n\n")[0]
        )
        self.print_footer()

    @property
    def current_quest(self) -> Optional[str]:
        if self.on_chapter == 1:
            if self.current_room_id == "driving_to_terminal":
                return "Waiting to arrive at the cruise terminal."
            else:
                return "Boarding the cruise ship."
        if self.on_chapter == 2:
            if "my_stateroom" not in world.visited_rooms:
                return "Exploring the Fortuna"
            elif "VIP Pass" not in world.inventory:
                return "Opening my safe"
            elif world.current_room_id != "vip_lounge":
                return "Making my way to the VIP Room"
            else:
                return "Chatting with other VIPs"
        if self.on_chapter < 5:
            return "Looking around and chatting on the VIP Tour."
        if self.on_chapter == 5:
            if "keycard" in self.inventory:
                return "Going to the engine room"
            else:
                return "Looking for an officer keycard"
        if self.on_chapter == 6:
            if "owner_stateroom" not in world.visited_rooms:
                return "Going to James Carrington's VIP room"
            else:
                password_string = ",".join(
                    [
                        x.upper() + "-" + str("poverty".index(x) + 1)
                        for x in list(self.password_letters_found)
                    ]
                )
                return f"Solving James Carrington's Safe Puzzle ({password_string})"

        return None

    def print_goal(self):
        if self.current_quest is not None:
            console.print(
                "Your current goal is: " + self.current_quest, style="bright_blue bold"
            )

    def print_header(self):
        self.print_goal()
        console.print(self.current_room.title, style="yellow bold")

    def print_footer(self):
        self.print_agents()
        self.print_exits()

    def exit_visible(self, exit):
        if exit.visible is not None:
            result_without_tokens, tokens = self.parse(exit.visible)

            if "False" in tokens:
                return False
        return True

    def print_exits(self):
        def pass_visible_test(direction_exit_pair: Tuple[str, Exit]):
            exit = direction_exit_pair[1]
            return self.exit_visible(exit)

        exit_text = [
            f"* {direction}: **{self.rooms[exit.room_uid].title}**"
            for direction, exit in filter(
                pass_visible_test, self.current_room.exits.items()
            )
        ]
        if len(exit_text) == 0:
            return
        console.print(Markdown("""**Exits:**\n""" + "\n".join(exit_text)))
        console.print()

    @property
    def agents_in_room(self) -> List[Agent]:
        def is_in_room(agent: Agent):
            return agent.room_id == self.current_room_id

        return list(filter(is_in_room, self.agents.values()))

    def print_agents(self):
        agent_text = [
            f"* {agent.name} is standing here.  " for agent in self.agents_in_room
        ]
        if len(agent_text) == 0:
            return
        console.print(Markdown("""**People Here:**\n""" + "\n".join(agent_text)))
        console.print()

    def describe_agent(self, agent: Agent):
        from gptif.converse import describe_character

        return describe_character(agent)

    def act_on(self, verb: str, look_object: str) -> bool:
        from gptif.converse import describe_character

        look_object_root = look_object.split(" ")[-1]

        # Check if we are looking at a person
        if verb == "look":
            for agent in self.agents_in_room:
                if agent.answers_to_name(look_object_root):
                    if agent.room_id == self.current_room_id:
                        description = describe_character(agent)
                        display_image_for_prompt(
                            "Portrait of character with description: " + description
                        )
                        self.play_sections([description])
                        return True

        hypernyms_set = get_hypernyms_set(look_object_root)
        # Loop through all scenery in the room, looking for a match
        for scenery in self.current_room.scenery:
            if (
                look_object.lower() in scenery.names
                or look_object_root.lower() in scenery.names
                or len(scenery.names.intersection(hypernyms_set)) > 0
            ):
                # Got a match
                scenery_action = None
                if verb.lower() in scenery.actions:
                    scenery_action = verb.lower()
                else:
                    verb_classes = get_verb_classes(verb.lower())
                    for action in scenery.actions.keys():
                        if len(verb_classes.intersection(get_verb_classes(action))) > 0:
                            scenery_action = action
                            break
                if scenery_action is not None:
                    self.play_sections(scenery.actions[scenery_action], "yellow")
                    if verb == "look":
                        display_image_for_prompt(
                            scenery.actions[scenery_action][0].split("\n\n")[0]
                        )
                    return True

        if verb == "look" and gptif.settings.FAKE_SCENERY:
            from gptif.converse import generate_fake_scenery

            fake_scenery = generate_fake_scenery(
                look_object,
                self.current_room.title,
                self.current_room.descriptions["Long"],
            )
            if fake_scenery is not None:
                if fake_scenery.count(".") > 1:
                    # Draw the object if the description is more than one sentence
                    display_image_for_prompt(fake_scenery)
                self.play_sections([fake_scenery])
                return True
        return False

    def play_sections(
        self,
        sections: List[str],
        style: Optional[str] = None,
        markdown: bool = True,
        insert_pauses: bool = False,
    ):
        for i, section in enumerate(sections):
            if i > 0 and insert_pauses:
                console.enqueue_ask_to_press_key()
            paragraph, tokens = self.parse(section)
            if len(paragraph) > 0 and paragraph != "None":
                if markdown:
                    console.print(Markdown(paragraph), style=style)
                else:
                    console.print(paragraph, style=style)
                console.print("")

    def persuade(self, agent: Agent):
        if agent.friend_points < 2:
            console.warning(
                f"You aren't close enough friends with {agent.name} to persuade them."
            )
            return

        if agent.uid == "port_security_officer":
            self.play_sections(
                """The security guard grabs your paperwork and begins reviewing it.  Other passengers are visibly annoyed at this.  One begins to approach the security desk, but Derrick holds up his palm.

**Derrick Williams (to other passenger):** VIP Guest, please wait your turn.

Derrick quickly scans your paperwork and hands you your room key.

**Derrick Williams:** Go north to board the ship, sir.""".split(
                    "{{< pagebreak >}}"
                ),
                "yellow",
                insert_pauses=True,
            )
        elif agent.uid == "research_scientist":
            if self.on_chapter >= 6:
                self.act_on("look", "painting")
            else:
                console.print(
                    "You would love to persuade David to accept you as his post-doc student, but now isn't the right time."
                )
        elif agent.uid == "vip_reporter":
            if self.on_chapter >= 6:
                if "v" in self.password_letters_found:
                    console.print("You thank Nancy again for her help.")
                else:
                    self.play_sections(
                        [
                            """
Nancy beams a large smile to you.  She has a smile that can make boulders give up their secrets.

**Nancy:** Hey, buddy!  Feeling better after what happened?  I got something I wanted to run by you:  I think there's hidden letters around the ship.

**Alfred:** Really?  What have you seen?

**Nancy:** Well it's mostly a rumor, but I have been talking to the crew and they all have the code 'V-3' etched into the crew bunks.  People have heard rumors of other letters around.  Let me know if you find anything!

**Alfred:** Will do, thanks Nancy!
"""
                        ]
                    )
                    self.password_letters_found.add("v")
            else:
                console.print(
                    "You feel that Nancy's detective work makes her a great friend to have, and you don't want to waste your favors until you really need something."
                )

    def start_chapter_one(self):
        self.active_agents = set(["taxi_driver", "port_security_officer"])
        self.current_room_id = "driving_to_terminal"
        self.time_in_room = 0
        self.on_chapter = 1
        self.time_in_chapter = 0

        with open("data/start_ch1.md", "r") as fp:
            sections = fp.read().split("{{< pagebreak >}}")
            self.play_sections(sections, insert_pauses=True)

        # Don't enqueue a press_key.  This needs to be cleared manually because it's only cleared in handle_input and this is the one command without input
        console.enqueue_press_key = False

    def start_ch2(self):
        self.active_agents = set(
            [
                "vip_room_safe",
                "owner_room_safe",
            ]
        )
        self.on_chapter = 2
        self.time_in_chapter = 0

        with open("data/start_ch2.md", "r") as fp:
            sections = fp.read().split("{{< pagebreak >}}")
            self.play_sections(sections, insert_pauses=True)

    def start_ch3(self):
        self.active_agents.update(
            [
                "tour_guide",
                "vip_reporter",
                "ex_convict",
                "research_scientist",
                "financier",
                "mercenary",
                "captain",
            ]
        )
        self.on_chapter = 3
        self.time_in_chapter = 0

        with open("data/start_ch3.md", "r") as fp:
            sections = fp.read().split("{{< pagebreak >}}")
            self.play_sections(sections, insert_pauses=True)

    def start_ch4(self):
        self.on_chapter = 4
        self.time_in_chapter = 0

    def start_ch5(self):
        self.on_chapter = 5
        self.time_in_chapter = 0

        with open("data/start_ch5.md", "r") as fp:
            sections = fp.read().split("{{< pagebreak >}}")
            self.play_sections(sections, insert_pauses=True)

        # Move some agents around
        self.agents["vip_reporter"].room_id = "pool_deck"
        self.agents["ex_convict"].room_id = "gym"
        self.agents["research_scientist"].room_id = "theater"
        self.agents["tour_guide"].room_id = None

    def start_ch6(self):
        self.on_chapter = 6
        self.time_in_chapter = 0

        with open("data/start_ch6.md", "r") as fp:
            sections = fp.read().split("{{< pagebreak >}}")
            self.play_sections(sections, insert_pauses=True)

    def start_ch7(self):
        self.on_chapter = 7
        self.time_in_chapter = 0

        with open("data/start_ch7.md", "r") as fp:
            sections = fp.read().split("{{< pagebreak >}}")
            self.play_sections(sections, insert_pauses=True)

    def check_can_board_ship(self):
        if self.friends_with("port_security_officer"):
            console.print(
                Markdown(
                    """The security officer waves you along with a smile.

```
Congratulations!  You solved your first puzzle.  More adventure awaits!
```"""
                )
            )
            return True
        console.print(
            'The security officer blocks your path.  "Excuse me sir, there\'s other things I need to do right now."\n'
        )
        console.print(
            Markdown(
                """```
This is your first empathy puzzle!  Ask the officer questions and learn what they want to talk about, then talk about what they like to talk about until they become your friend.
```"""
            )
        )
        return False

    def friends_with(self, agent_name: str):
        return self.agents[agent_name].friend_points >= 2


class ScriptId(IntEnum):
    stationary = 0
    vip_tour_guest = 1
    tour_guide = 2
    nancy = 3
    financier = 4
    mercenary = 5
    captain = 6


class MovementScript:
    def step(self, agent: Agent):
        raise NotImplementedError()


class TourGuideMovementScript(MovementScript):
    def __init__(self):
        pass

    def step(self, agent: Agent):
        if world.on_chapter == 4:
            time_movement_map = {
                3: "down",
                6: "down",
                9: "down",
                12: "down",
                15: "down",
                18: "north",
            }
            if world.time_in_chapter in time_movement_map:
                direction = time_movement_map[world.time_in_chapter]
                if world.time_in_chapter < 7:
                    tour_group = [
                        world.agents["vip_reporter"],
                        world.agents["ex_convict"],
                        world.agents["research_scientist"],
                        world.agents["mercenary"],
                        None,
                    ]
                else:
                    tour_group = [
                        world.agents["vip_reporter"],
                        world.agents["ex_convict"],
                        world.agents["research_scientist"],
                        None,
                    ]
                random.shuffle(tour_group)
                # Move everyone to the pool deck
                console.print(Markdown("**June:** Come along, everyone."))
                world.send_agent(agent, direction)
                [
                    world.send_agent(agent, direction)
                    if agent is not None
                    else world.go(direction)
                    for agent in tour_group
                ]

                # Once we have arrived, have June give a speech
                if world.current_room_id == "mess_hall_hallway":
                    console.print(
                        Markdown(
                            "**June:** This corridor leads to the mess hall, where the crew can recover after a long shift."
                        )
                    )
                elif world.current_room_id == "atrium":
                    console.print(
                        Markdown(
                            "**June:** This is the first room that guests see when they board The Fortuna.  The fountain is truly opulent."
                        )
                    )
                elif world.current_room_id == "promenade":
                    console.print(
                        Markdown(
                            "**June:** After the tour, be sure to sample some of our finest liquors at The Buoyant Bartender."
                        )
                    )
                elif world.current_room_id == "stateroom_deck":
                    console.print(
                        Markdown(
                            "**June:** I hope you all are enjoying your luxury staterooms. It's truly a magical experience."
                        )
                    )
                elif world.current_room_id == "pool_deck":
                    console.print(
                        Markdown(
                            "**June:** The hot tubs are particularly popular close to sunset of rest and relaxation.  Sunset is around 7:50pm today ship-time, so set your watches."
                        )
                    )


@dataclass
class Movement:
    starting_room: Optional[str]
    script_id: ScriptId
    script: Optional[MovementScript]

    @classmethod
    def from_yaml(cls, yaml):
        script_id = ScriptId[yaml.get("script_id", "stationary")]
        script = None
        if script_id == ScriptId.tour_guide:
            script = TourGuideMovementScript()
        return Movement(yaml["starting_room"], script_id, script)

    def step(self, agent: Agent):
        if self.script is None:
            return
        self.script.step(agent)
