#!/usr/bin/env python

"""Main tvnamer utility functionality
"""

import os
import sys
import logging

try:
    import readline
except ImportError:
    pass

try:
    import json
except ImportError:
    import simplejson as json

from tvdb_api import Tvdb

import cliarg_parser
from config_defaults import defaults

from unicode_helper import p
from utils import (Config, FileFinder, FileParser, Renamer, warn,
applyCustomInputReplacements, formatEpisodeNumbers, makeValidFilename)

from tvnamer_exceptions import (ShowNotFound, SeasonNotFound, EpisodeNotFound,
EpisodeNameNotFound, UserAbort, InvalidPath, NoValidFilesFoundError,
InvalidFilename, DataRetrievalError)


def log():
    """Returns the logger for current file
    """
    return logging.getLogger(__name__)


def getDestinationFolder(episode):
    """Constructs the location to move/copy the file
    """

    #TODO: Write functional test to ensure this valid'ifying works
    def wrap_validfname(fname):
        """Wrap the makeValidFilename function as it's called twice
        and this is slightly long..
        """
        if Config['move_files_lowercase_destination']:
            fname = fname.lower()
        return makeValidFilename(
            fname,
            normalize_unicode = Config['normalize_unicode_filenames'],
            windows_safe = Config['windows_safe_filenames'],
            custom_blacklist = Config['custom_filename_character_blacklist'],
            replace_with = Config['replace_invalid_characters_with'])


    # Calls makeValidFilename on series name, as it must valid for a filename
    destdir = Config['move_files_destination'] % {
        'seriesname': wrap_validfname(episode.seriesname),
        'seasonnumber': episode.seasonnumber,
        'episodenumbers': wrap_validfname(formatEpisodeNumbers(episode.episodenumbers))
    }
    return destdir


def doRenameFile(cnamer, newName):
    """Renames the file. cnamer should be Renamer instance,
    newName should be string containing new filename.
    """
    try:
        cnamer.newName(newName)
    except OSError, e:
        warn(e)


def doMoveFile(cnamer, destDir, getPathPreview = False):
    """Moves file to destDir"""
    if not Config['move_files_enable']:
        raise ValueError("move_files feature is disabled but doMoveFile was called")

    if Config['move_files_destination'] is None:
        raise ValueError("Config value for move_files_destination cannot be None if move_files_enabled is True")

    try:
        return cnamer.newPath(destDir, getPathPreview = getPathPreview)
    except OSError, e:
        warn(e)


def confirm(question, options, default = "y"):
    """Takes a question (string), list of options and a default value (used
    when user simply hits enter).
    Asks until valid option is entered.
    """
    # Highlight default option with [ ]
    options_str = []
    for x in options:
        if x == default:
            x = "[%s]" % x
        if x != '':
            options_str.append(x)
    options_str = "/".join(options_str)

    while True:
        p(question)
        p("(%s) " % (options_str), end="")
        try:
            ans = raw_input().strip()
        except KeyboardInterrupt, errormsg:
            p("\n", errormsg)
            raise UserAbort(errormsg)

        if ans in options:
            return ans
        elif ans == '':
            return default


def processFile(tvdb_instance, episode):
    """Gets episode name, prompts user for input
    """
    p("#" * 20)
    p("# Processing file: %s" % episode.fullfilename)

    if len(Config['input_filename_replacements']) > 0:
        replaced = applyCustomInputReplacements(episode.fullfilename)
        p("# With custom replacements: %s" % (replaced))

    p("# Detected series: %s (%s)" % (episode.seriesname, episode.number_string()))

    try:
        episode.populateFromTvdb(tvdb_instance, force_name=Config['force_name'], series_id=Config['series_id'])
    except (DataRetrievalError, ShowNotFound), errormsg:
        if Config['always_rename'] and Config['skip_file_on_error'] is True:
            warn("Skipping file due to error: %s" % errormsg)
            return
        else:
            warn(errormsg)
    except (SeasonNotFound, EpisodeNotFound, EpisodeNameNotFound), errormsg:
        # Show was found, so use corrected series name
        if Config['always_rename'] and Config['skip_file_on_error']:
            warn("Skipping file due to error: %s" % errormsg)
            return

        warn(errormsg)

    cnamer = Renamer(episode.fullpath)
    newName = episode.generateFilename()

    if newName == episode.fullfilename:
        p("#" * 20)
        p("Skipping: existing filename is correct: %s" % episode.fullfilename)
        p("#" * 20)
    else:
        p("#" * 20)
        p("Old filename: %s" % episode.fullfilename)

        if len(Config['output_filename_replacements']) > 0:
            # Show filename without replacements
            p("Before custom output replacements: %s" % (episode.generateFilename(preview_orig_filename = False)))

        p("New filename: %s" % newName)

        if Config['always_rename']:
            doRenameFile(cnamer, newName)
            if Config['move_files_enable']:
                doMoveFile(cnamer = cnamer, destDir = getDestinationFolder(episode))
            return

        ans = confirm("Rename?", options = ['y', 'n', 'a', 'q'], default = 'y')

        shouldRename = False
        if ans == "a":
            p("Always renaming")
            Config['always_rename'] = True
            shouldRename = True
        elif ans == "q":
            p("Quitting")
            raise UserAbort("User exited with q")
        elif ans == "y":
            p("Renaming")
            shouldRename = True
        elif ans == "n":
            p("Skipping")
        else:
            p("Invalid input, skipping")

        if shouldRename:
            doRenameFile(cnamer, newName)

            if Config['move_files_enable']:
                newPath = getDestinationFolder(episode)
                doMoveFile(cnamer = cnamer, destDir = newPath, getPathPreview = True)

                if Config['move_files_confirmation']:
                    ans = confirm("Move file?", options = ['y', 'n', 'q'], default = 'y')
                else:
                    ans = 'y'

                if ans == 'y':
                    p("Moving file")
                    doMoveFile(cnamer, newPath)
                elif ans == 'q':
                    p("Quitting")
                    raise UserAbort("user exited with q")


def findFiles(paths):
    """Takes an array of paths, returns all files found
    """
    valid_files = []

    for cfile in paths:
        cur = FileFinder(
            cfile,
            with_extension = Config['valid_extensions'],
            filename_blacklist = Config["filename_blacklist"],
            recursive = Config['recursive'])

        try:
            valid_files.extend(cur.findFiles())
        except InvalidPath:
            warn("Invalid path: %s" % cfile)

    if len(valid_files) == 0:
        raise NoValidFilesFoundError()

    # Remove duplicate files (all paths from FileFinder are absolute)
    valid_files = list(set(valid_files))

    return valid_files


def tvnamer(paths):
    """Main tvnamer function, takes an array of paths, does stuff.
    """
    # Warn about move_files function
    if Config['move_files_enable']:
        import warnings
        warnings.warn("The move_files feature is still under development. "
            "Be very careful with it.\n"
            "It has not been heavily tested, and is not recommended for "
            "general use yet.")

    if Config['force_name'] is not None:
        import warnings
        warnings.warn("The --name argument is a temporary solution, and will"
            "be removed at some point in the future. Do no depend on it")

    p("#" * 20)
    p("# Starting tvnamer")

    episodes_found = []

    for cfile in findFiles(paths):
        parser = FileParser(cfile)
        try:
            episode = parser.parse()
        except InvalidFilename, e:
            warn("Invalid filename: %s" % e)
        else:
            if episode.seriesname is None:
                warn("Parsed filename did not contain series name, skipping: %s" % cfile)
            else:
                episodes_found.append(episode)

    if len(episodes_found) == 0:
        raise NoValidFilesFoundError()

    p("# Found %d episode" % len(episodes_found) + ("s" * (len(episodes_found) > 1)))

    # Sort episodes by series name, season and episode number
    episodes_found.sort(key = lambda x: x.sortable_info())

    tvdb_instance = Tvdb(
        interactive = not Config['select_first'],
        search_all_languages = Config['search_all_languages'],
        language = Config['language'])

    for episode in episodes_found:
        processFile(tvdb_instance, episode)
        p('')

    p("#" * 20)
    p("# Done")


def main():
    """Parses command line arguments, displays errors from tvnamer in terminal
    """
    opter = cliarg_parser.getCommandlineParser(defaults)

    opts, args = opter.parse_args()

    if opts.verbose:
        logging.basicConfig(
            level = logging.DEBUG,
            format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    else:
        logging.basicConfig()

    # If a config is specified, load it, update the defaults using the loaded
    # values, then reparse the options with the updated defaults.
    default_configuration = os.path.expanduser("~/.tvnamer.json")

    if opts.loadconfig is not None:
        # Command line overrides loading ~/.tvnamer.json
        configToLoad = opts.loadconfig
    elif os.path.isfile(default_configuration):
        # No --config arg, so load default config if it exists
        configToLoad = default_configuration
    else:
        # No arg, nothing at default config location, don't load anything
        configToLoad = None

    if configToLoad is not None:
        p("Loading config: %s" % (configToLoad))
        try:
            loadedConfig = json.load(open(configToLoad))
        except ValueError, e:
            p("Error loading config: %s" % e)
            opter.exit(1)
        else:
            # Config loaded, update optparser's defaults and reparse
            defaults.update(loadedConfig)
            opter = cliarg_parser.getCommandlineParser(defaults)
            opts, args = opter.parse_args()

    # Decode args using filesystem encoding (done after config loading
    # as the args are reparsed when the config is loaded)
    args = [x.decode(sys.getfilesystemencoding()) for x in args]

    # Save config argument
    if opts.saveconfig is not None:
        p("Saving config: %s" % (opts.saveconfig))
        configToSave = dict(opts.__dict__)
        del configToSave['saveconfig']
        del configToSave['loadconfig']
        del configToSave['showconfig']
        json.dump(
            configToSave,
            open(opts.saveconfig, "w+"),
            sort_keys=True,
            indent=4)

        opter.exit(0)

    # Show config argument
    if opts.showconfig:
        for k, v in opts.__dict__.items():
            p(k, "=", str(v))
        return

    # Process values
    if opts.batch:
        opts.select_first = True
        opts.always_rename = True

    # Update global config object
    Config.update(opts.__dict__)

    if len(args) == 0:
        opter.error("No filenames or directories supplied")

    try:
        tvnamer(paths = sorted(args))
    except NoValidFilesFoundError:
        opter.error("No valid files were supplied")
    except UserAbort, errormsg:
        opter.error(errormsg)

if __name__ == '__main__':
    main()
