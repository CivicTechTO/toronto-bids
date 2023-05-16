(ns frontend.common
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [clj-http.client :as client])
)

(defn head [local-base title css-file]
	[:head
		[:title title]
		(page/include-css (str local-base css-file))
	]
)

(defn stay [limit offset]
	offset
)

(defn forward [limit offset]
	(+ offset limit)
)

(defn back [limit offset]
	(max 0 (- offset limit))
)
