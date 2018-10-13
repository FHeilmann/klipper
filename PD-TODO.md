TODO
=====
1. [X] 1. Home X, Y, Z, All

2. [X] 2. G32 levelling

3. [ ] 3. M408 needs to finish type 0 implementation

4. [ ] 4. M408 needs to implement type 1

5. [X] 5. M408 needs to identify proper number of tools

6. [X] 6. M408 needs to be able to report to PanelDue during active print. Currently get queued up until print finishes.

7. [X] 7. Set active bed temperature 

8. [X] 8. Set standby bed temperature

9. [X] 9. Set active tool temperature (G10 P# S###)

10. [X] 10. Set standby tool temperature (G10 P# R###)

11. [X] 11. Log messages to console

12. [X] 12. View macros

13. [X] 13. Execute macros

14. [X] 14. Move commands

15. [X] 15. show SD card files (works when virtual SD card mapped)

16. [X] 16. start print from SD card

17. [ ] 17. show file stats (size, sliced by, etc)

18. [X] 18. Pause print

19. [X] 19. Baby stepping

20. [X] 20. Cancel print

21. [X] 21. Extrusion settings (now tested!)

22. [ ] 22. Extra Documentation. Make sure to mention 3.3V voltage Levels

23. [ ] 23. Create a document that shows how to connect a PanelDue to the rpi onboard UART

24. [X] 24. Have M408 detect "Paused" state so that PD can resume (note: Klipper is in "Ready" state)

25. [ ] 25. Show homing states

26. [ ] 26. Show fan speeds during print (is this part of M408 type 1?)

27. [ ] 27. Have SD card listing only show .gcode files (add filter)

28. [ ] 28. SD card - add ability to navigate into sub-folders. Note: May also affect "start print" command (M32)

29. [ ] 28. Figure why bed standby temp doesn't update on PD (wrong mapping?)


Nice to Haves
====

1. [ ] 1. Octoprint print manager then allow user to decide between Virtual SD and Octoprint.
