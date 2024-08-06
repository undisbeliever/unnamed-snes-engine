unnamed-snes-game tech demo
===========================

A simple single-screen top-down game-demo for the SNES.

![unnamed-tech-demo screenshot](screenshot.jpeg)



Build Requirements
==================
 * [Wiz](https://github.com/wiz-lang/wiz), a high level assembly language (included as a git submodule)
 * A C++17 compiler (see the [wiz README](https://github.com/wiz-lang/wiz#building-source) for more details)
 * GNU Make
 * Python 3
 * [Python Pillow Imaging Library](https://pillow.readthedocs.io/en/stable/)
 * [python websocket-client](https://websocket-client.readthedocs.io/en/latest/index.html)
 * [python watchdog](https://python-watchdog.readthedocs.io/en/stable/)



License
=======

The unnamed-snes-engine is Copyright (c) 2022, Marcus Rowe <undisbeliever@gmail.com> and released
under the [MIT license](LICENSE).

This engine uses the following open source code:
 * [Terrific Audio Driver](https://github.com/undisbeliever/terrific-audio-driver/),
   copyright (c) 2023, Marcus Rowe.
    * Driver code used by the game is licensed under the [zlib License](https://github.com/undisbeliever/terrific-audio-driver/blob/main/audio-driver/LICENSE).
    * The compiler and GUI are licensed under the [MIT License](https://github.com/undisbeliever/terrific-audio-driver/blob/main/docs/licenses-short.md).
    * The Terrific Audio Driver makes uses multiple third party open source projects.
      See [terrific-audio-driver/docs/licenses.md](https://github.com/undisbeliever/terrific-audio-driver/blob/main/docs/licenses.md)
      for more details.

The included unnamed-snes-game tech-demo is Copyright (c) 2022, Marcus Rowe <undisbeliever@gmail.com>.
 * The code is released under the [MIT license](LICENSE).
 * [Unless otherwise noted](CREDITS.md); the art, levels and sound-effects included in the
   [tech-demo/resources](tech-demo/resources/) directory is created by undisbeliever and licensed under a
   [Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) license](https://creativecommons.org/licenses/by-sa/4.0/)
 * Music by KungFuFurby, licensed under CC BY-SA 4.0. To view a copy of this license, visit http://creativecommons.org/licenses/by-sa/4.0/
 * BRR samples have been sourced from multiple places, please see the [included credits file](CREDITS.md) for the full details.


