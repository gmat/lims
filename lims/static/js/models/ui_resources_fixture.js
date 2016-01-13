{   
  "home": {
    "title": "Screensaver LIMS",
    "route": "/",
    "view": "HomeView",
    "content_header": "Welcome",
    "description": "Menu starting point"
  },
  "about": {
    "title": "ICCB-L Screensaver LIMS",
    "route": "about",
    "view": "AboutView",
    "content_header": "ICCB-L Screensaver LIMS",
    "description": "About page"
  },  
  "reports": {
    "title": "Reports",
    "route": "",
    "view": "AboutView",
    "content_header": "ICCB-L Screensaver LIMS",
    "description": "Reports"
  },

  "screensaveruser": {
        "header_message": "All users (Screeners and Staff)",
        "title": "Screensaver Users",
        "route": "list/screensaveruser",
        "list_view": "ListView",
        "detailView": "UserView",
        "api_resource": "screensaveruser",
        "url_root": "/db/api/v1",
        "description": "View user information",
        "options": { }
    },

    "screeners": {
        "header_message": "Screening Users",
        "title": "Screeners",
        "route": "list/screeners",
        "list_view": "ListView",
        "detailView": "UserView",
        "api_resource": "screensaveruser",
        "url_root": "/db/api/v1",
        "description": "View user information",
        "options": { 
          "search": {"screeningroomuser__isnull": "False"} }
    },

    "staff": {
        "header_message": "Staff",
        "title": "Staff Users",
        "route": "list/staff",
        "list_view": "ListView",
        "detailView": "UserView",
        "api_resource": "screensaveruser",
        "url_root": "/db/api/v1",
        "description": "View user information",
        "options": { 
          "search": {"administratoruser__isnull": "False"} }
    },
    "screen": {
        "header_message": "All screens (Small Molecule and RNAi)",
        "title": "Screens",
        "route": "list/screen",
        "list_view": "ListView",
        "detailView": "ScreenView",
        "api_resource": "screen",
        "url_root": "/db/api/v1",
        "description": "View screen information",
        "options": { 
          "search": { "project_phase__ne": "annotation" }
        }
    },
    "small_molecule_screens": {
        "header_message": "Small Molecule Screens",
        "title": "Small Molecule Screen",
        "route": "list/small_molecule_screens",
        "list_view": "ListView",
        "detailView": "DetailView",
        "api_resource": "screen",
        "url_root": "/db/api/v1",
        "description": "View small molecule screen information",
        "options": { 
          "search": { "screen_type__in": "small_molecule",
                      "project_phase__ne": "annotation"
                    }
        }
    },
    "rnai_screens": {
        "header_message": "All screens (Small Molecule and RNAi)",
        "title": "RNAi Screen",
        "route": "list/rnai_screens",
        "list_view": "ListView",
        "detailView": "DetailView",
        "api_resource": "screen",
        "url_root": "/db/api/v1",
        "description": "View rnai screen information",
        "options": { 
          "search": { "screen_type__in": "rnai",
                      "project_phase__ne": "annotation" }
        }
    },
    "library": {
        "header_message": "All libraries (Small Molecule and RNAi)",
        "title": "Libraries",
        "route": "list/library",
        "list_view": "ListView",
        "detailView": "LibraryView",
        "api_resource": "library",
        "url_root": "/db/api/v1",
        "description": "View library information",
        "options": { "order": ["short_name"] }
    },
    "smallmoleculelibrary": {
        "header_message": "Small Molecule Libraries",
        "title": "Small Molecule",
        "route": "list/smallmoleculelibrary",
        "list_view": "ListView",
        "detailView": "LibraryView",
        "api_resource": "library",
        "url_root": "/db/api/v1",
        "description": "View Small Molecule Library information",
        "options": { 
          "rpp": 500, 
          "includes": ["-screen_type","-is_pool"],
          "order": ["-start_plate"], 
          "search": { 
            "screen_type__in": "small_molecule"
           } 
        }
    },
    "rnalibrary": {
        "header_message": "RNAi Libraries",
        "title": "RNAi",
        "route": "list/rnalibrary",
        "list_view": "ListView",
        "detailView": "LibraryView",
        "api_resource": "library",
        "url_root": "/db/api/v1",
        "description": "View RNAi library information",
        "options": { 
          "rpp": 500, 
          "search": { "screen_type__in": "rnai"}, 
          "includes": ["-screen_type"],
          "order": ["short_name"]  
        }
    },
    "well": {
      "header_message": "Wells",
      "title": "Well",
      "route": "list/well",
      "list_view": "ListView",
      "detailView": "WellView",
      "api_resource": "well",
      "url_root": "/db/api/v1",
      "description": "Well information",
      "options": { 
        "rpp_selections" : [24,96,384,1000],
        "rpp": 24,
        "order": []  }
    },
    "copywell": {
      "options": { 
        "rpp_selections" : [24,96,384,1000],
        "rpp": 24 }
    },
    "reagent": {
      "header_message": "Wells",
      "title": "Well",
      "route": "list/reagent",
      "list_view": "ListView",
      "detailView": "WellView",
      "api_resource": "reagent",
      "url_root": "/db/api/v1",
      "description": "Reagent Well information",
      "options": { 
        "rpp_selections" : [24,96,384,1000],
        "rpp": 24,
        "order": []  }
    },
    "librarycopyplate": {
      "options": { 
        "order": []  }
    },
    "librarycopy": {
      "options": { 
        "order": []  }
    },
    "userchecklistitem": {
      "options": {
        "rpp": 200,
        "search": {"item_group__ne": "legacy"}
      }
    },
    "detail_test": {
      "header_message": "Detail Test View",
      "title": "Detail Test View",
      "route": "list/detailtest",
      "list_view": "ListView",
      "detailView": "DetailTestView",
      "api_resource": "reagent"
    }
}