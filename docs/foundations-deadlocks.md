# Deadlocks

!!! question "Already know this?"
    1. What four conditions must all hold for a deadlock?
    2. What is "hold and wait", and how do you design it away?
    3. Why is a deadlock worse than a crash?

    All three easy? Skip to [The four globals, two regimes](foundations-regimes.md).

## The concept

A **deadlock** is a circle of waiting: A holds something B needs while
waiting for something B holds. Nobody errs, nobody crashes — everything
simply stops, forever, with no stack trace pointing anywhere. That silence
is what makes deadlocks worse than crashes: a crash tells you where; a
deadlock tells you nothing.

The ingredient that matters most in practice is **hold-and-wait**: taking
a resource, then blocking for another while still holding the first. Code
that never waits while holding cannot complete the circle. The strongest
designs don't detect deadlocks — they make one of the ingredients
impossible.

## A worked example: how a careful design still deadlocks

The tempting way to share one working directory between parallel tasks is a
clever lock: tasks needing the same directory *share* a hold; a nested block
can *escalate* to a different directory by waiting for its co-holders to
leave; a fan-out child re-targeting under its parent's hold is detected and
refused. Every rule is individually sound. Composing two of them is fatal:

1. A parent takes the lock at directory `A`, then fans out children and
   waits for them — legal.
2. A child joins the hold at `A` (same target — "not even a conflict") —
   legal.
3. The child then nests a block for directory `B`: an escalation, which
   waits for its co-holders to leave. Its co-holder is the parent. The
   parent is waiting for the child.

A certain, silent deadlock built from two blessed moves — and since most
projects point every task at one directory, that setup is the *common
case*, not a corner. No detection rule fires, because each rule is checked
at the moment of one move, and the circle only exists across both.

footman changes the ingredient instead. Serialisation is **declared on the
task and granted at the task boundary**, before the body runs — the
scheduler *orders* claims instead of letting bodies contend mid-flight. A
resource acquired only at boundaries can always be scheduled; hold-and-wait
needs a mid-body wait, and there is none. Lineage makes the fan-out case
safe by construction: a child of a lane holder *extends* the hold rather
than contending with it. And the waits that remain are never silent — a
queued task prints who holds what after a couple of seconds, because an
invisible wait is a deadlock you haven't confirmed yet.

## The one rule

**Never wait while holding; declare, don't contend — and make every
remaining wait say its name.**
