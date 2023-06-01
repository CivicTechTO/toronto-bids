# Toronto Bids Application

There are three components to the "application":

1. A MySQL database containing all of the call data.
2. An API program used to query the database.
3. A frontend that uses API calls to search through the calls.

## MySQL database

Information on setting up the MySQL database is provided in the `db` folder. These instructions will only set you up with a sampling of the complete call data.

## API program

The API program is located in the `server/toronto-bids` folder. It is written in [Clojure](https://clojure.org/guides/install_clojure) and uses the [Leiningen](https://leiningen.org/) build tool.

The `stuff.clj` folder will need to be updated with login information for the MySQL database. Using the username and password of `bids` as shown in `db/README.md`, the db-spec should become this:

```
(str "jdbc:mysql:///" db-name "?user=bids&password=bids")
```

The program can either be compiled to a .jar file using `lein uberjar`, or can be executed immediately with the command `lein run test_bids 8000` (to access the `test_bids` database and respond to API calls on port 8000).

## Frontend

There are multiple versions of the frontend available:

### Clojure

This frontend is located in the `site-clj` folder. Leiningen is used as a build tool.

Either compile the frontend to a .jar using `lein uberjar`, or execute it immediately with the command `lein run http://localhost:8000/api/ 8001 ""`. The first argument points to the running API program, `8001` sets the port to run the frontend on, and the empty quotes set the local base.

You can access the running frontend by going to `http://localhost:8001/` in a web browser.

### Angular

The `site` folder contains an Angular webapp that's a rough start for something we might want to use as a front end to access bid data.

#### Prerequisites

To run the project, ensure your machine meets the following prerequisites (these are all just the prereqs to run a standard Angular App)

- Install NodeJS and the Node Package Manager on the machine this is running on (https://nodejs.org/en/download)

- Download the Angular CLI, instructions found here - (https://github.com/angular/angular-cli)

#### Building the webapp

- If this is your first time running the webapp, make sure that you have NodeJS, npm and the Angular CLI installed on your machine. 
 
- Open a terminal in this folder and run ```npm install```. This will ensure download all of the dependencies that you need to run this project into a folder called 'node_modules'. 

- Run the command `ng serve`. This will stand up a webserver that the app will be accessible on for local access. By default the webserver will run on port 4200, but you can change this by supplying the `--port` parameter when you invoke `ng serve`.

### PHP

A PHP frontend is available in the `site-php` folder. This frontend uses its own API implementation to access the bids database.

