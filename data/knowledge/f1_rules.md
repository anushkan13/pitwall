# Pitwall F1 knowledge file

A small, curated set of rules and history, written for the 2025 season. The
Historian agent answers rules and history questions only from these entries
(via the `lookup_rules` MCP tool) and cites them by id. If a question isn't
covered here, it says it doesn't know.

Format: each entry starts with `## <id>: <title>`, then a `keywords:` line,
then the text. Keep entries short and factual. Any number in an answer must
appear in the entry it cites.

## drs: DRS (Drag Reduction System)
keywords: drs, drag reduction, rear wing, flap, overtaking aid, detection point, activation zone
DRS is a flap in the rear wing that the driver can open to cut drag and gain straight-line speed. It can only be used in designated DRS zones, and only when the car is within 1 second of the car ahead at the detection point before that zone. Race control enables it two laps after the start or after a Safety Car restart, and can disable it, for example in wet conditions. The 2025 season is the last with DRS; the 2026 rules replace it.

## undercut: The undercut
keywords: undercut, pit earlier, fresh tires, out lap, jump, pass in the pits
An undercut is pitting before the car you are racing so that your laps on fresh tires are faster than theirs on worn tires. If you gain enough time before they stop, you come out ahead when they make their own stop. It works best when fresh tires are much quicker than worn ones and the gap is small, typically a few seconds or less.

## overcut: The overcut
keywords: overcut, stay out, pit later, longer stint, clear air
An overcut is staying out longer than the car you are racing and pitting after them. It works when your worn tires are still quick, when their fresh tires take time to warm up, or when they rejoin in traffic, so you build enough of a lead to stay ahead after your own stop.

## pit_stop: Pit stops and the pit lane
keywords: pit stop, pit lane, speed limit, stationary, box, pit loss, tire change
In a pit stop the car drives through the pit lane, stops in its box for a tire change, and rejoins. The pit lane has a speed limit, usually 80 km/h and lower at some tight circuits. The total time lost compared with staying on track (the pit loss) is mostly the pit-lane drive; the stationary time for a good tire change is usually around 2 to 3 seconds.

## safety_car: Safety Car
keywords: safety car, sc, neutralised, bunch up, restart, cheap pit stop
The Safety Car is deployed when the track needs to be neutralised, for example after a crash. The field lines up behind it and overtaking is not allowed. Because everyone is driving slowly, pitting under the Safety Car costs much less time than usual, which often triggers a wave of stops. When it comes in, the leader controls the restart.

## vsc: Virtual Safety Car
keywords: vsc, virtual safety car, delta, neutralised
Under a Virtual Safety Car there is no physical car; every driver must stay above a minimum time (a delta) set by race control, which slows the field to a similar pace while keeping the gaps roughly the same. Overtaking is not allowed. A pit stop under a VSC costs less time than under green flags, though usually not as little as under a full Safety Car.

## red_flag: Red flag
keywords: red flag, stopped, suspended, restart, free tire change
A red flag stops the session. Cars return slowly to the pit lane and wait. During the stoppage teams are allowed to change tires, so a red flag can hand a free tire change to drivers who had not yet stopped.

## tire_compounds: Tire compounds
keywords: tires, tyres, compounds, soft, medium, hard, intermediate, wet, pirelli, c1, c6, colours
Pirelli supplies all teams. For each event three dry compounds are chosen from a range of six, C1 (hardest) to C6 (softest), and named soft (red markings), medium (yellow) and hard (white). Softer tires are faster but wear more quickly. For a wet track there are intermediates (green), for damp or light rain, and full wets (blue), for heavy rain.

## compound_rule: Two-compound rule
keywords: two compounds, mandatory stop, compound rule, must use, dry race
In a dry race each driver must use at least two different dry compounds, which in practice forces at least one pit stop. The rule does not apply if intermediate or wet tires are used during the race.

## points: Points system
keywords: points, scoring, 25, top ten, fastest lap point, championship points
In a Grand Prix the top ten finishers score points: 25, 18, 15, 12, 10, 8, 6, 4, 2 and 1. From 2025 there is no longer a bonus point for the fastest lap.

## sprint: Sprint races
keywords: sprint, sprint race, sprint qualifying, saturday, sprint points, sprint weekend
At sprint weekends there is a short race of about 100 km on the Saturday, with its own Sprint Qualifying session setting the grid. The top eight score points: 8, 7, 6, 5, 4, 3, 2 and 1. There were six sprint weekends in 2025.

## qualifying: Qualifying format
keywords: qualifying, q1, q2, q3, knockout, pole, grid
Qualifying sets the grid in three knockout parts. With 20 cars, the slowest five are eliminated after Q1 and five more after Q2, and the remaining ten fight for pole position in Q3.

## penalties: Penalties
keywords: penalty, time penalty, five seconds, ten seconds, drive through, stop and go, stewards
The stewards can hand out time penalties of 5 or 10 seconds. A driver can serve one at their next pit stop, where the crew must wait out the penalty before working on the car; otherwise it is added to their race time. Heavier penalties are a drive-through (drive down the pit lane without stopping) and a stop-and-go (stop in the box for a set time with no work allowed).

## track_limits: Track limits
keywords: track limits, white lines, lap deleted, black and white flag
A car must keep at least part of itself within the white lines that mark the track edge. Breaking track limits gets the lap time deleted in qualifying; in the race, repeated offences bring a black-and-white warning flag and then a time penalty.

## blue_flags: Blue flags
keywords: blue flag, lapped, backmarker, let through
A blue flag tells a driver who is about to be lapped that a faster car is behind and they must let it through. Ignoring blue flags is penalised.

## parc_ferme: Parc fermé
keywords: parc ferme, setup, after qualifying, changes
From the start of qualifying until the race, cars are under parc fermé conditions: teams can only make limited changes to the setup. Major changes force a start from the pit lane.

## classification: Classification and retirements
keywords: dnf, retired, classified, ninety percent, 90%, finish
A car that retires is still classified if it completed at least 90% of the winner's race distance. DNF means did not finish; DSQ means disqualified.

## gap_interval: Gap and interval
keywords: gap, interval, timing, leader, car ahead, delta
On the timing screens the interval is the time to the car directly ahead, and the gap is the time to the race leader.

## championships: The two championships
keywords: championship, drivers championship, constructors, title, standings
There are two championships: the Drivers' Championship for individual drivers, and the Constructors' Championship for teams, where a team scores the points of both of its cars.

## history_first_race: The first World Championship race
keywords: history, first race, 1950, silverstone, first world championship
The first round of the Formula 1 World Championship was the 1950 British Grand Prix at Silverstone.

## history_most_titles: Most drivers' titles
keywords: history, most titles, record, schumacher, hamilton, seven, champions
The record for most Drivers' Championships is seven, shared by Michael Schumacher and Lewis Hamilton.
